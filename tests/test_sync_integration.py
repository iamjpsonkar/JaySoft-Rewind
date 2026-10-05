"""Cross-adapter Flask replay and WSGI protocol conformance."""

import io
import sqlite3

import httpx
import pytest
from flask import Flask, request
from sqlalchemy import text
from werkzeug.test import EnvironBuilder

from rewind import CapturePolicy, LocalStore, Retention, Rewind
from rewind.adapters.sqlalchemy import create_engine
from rewind.context import current
from rewind.errors import ReplayDivergence
from rewind.runner import ReplayTarget, replay_file


@pytest.fixture
def rewind(tmp_path):
    return Rewind(
        application="sync-integration", code_paths=[__file__],
        store=LocalStore(tmp_path / "snapshots"), policy=CapturePolicy.synthetic(),
        retain=Retention(always=True),
    )


def serve(app, incoming):
    statuses = []
    response = app(incoming, lambda status, headers, exc_info=None: (
        statuses.append(status) or (lambda body: None)
    ))
    try:
        body = b"".join(response)
    finally:
        response.close()
    return statuses, body


def saved(rewind):
    return rewind.store.load(rewind.store.ids()[0])


def combined_app(rewind, calls, *, refuse_live=False):
    app = Flask(__name__)

    def live(request):
        if refuse_live:
            raise AssertionError("fresh replay reached a live transport")
        calls.append(request.url.path)
        return httpx.Response(503, json={"reason": "synthetic-outage"})

    @app.post("/checkout")
    def checkout():
        amount = request.get_json()["amount"]
        engine = create_engine(dependency="pricing")
        try:
            with engine.connect() as connection:
                total = connection.execute(text("select :amount + 2"), {"amount": amount}).scalar()
        finally:
            engine.dispose()
        with httpx.Client(transport=rewind.httpx_sync_transport(
            httpx.MockTransport(live), dependency="gateway"
        )) as client:
            gateway = client.post("https://gateway.invalid/charge", json={"amount": total})
        return {
            "amount": total, "gateway": gateway.json(),
            "request_id": rewind.sources.uuid4(),
        }, gateway.status_code

    return app


def combined_replay_target():
    rewind = Rewind(
        application="sync-integration", code_paths=[__file__],
        policy=CapturePolicy.synthetic(), retain=Retention(always=True),
    )
    return ReplayTarget(rewind, combined_app(rewind, [], refuse_live=True), kind="wsgi")


def test_flask_sqlalchemy_and_sync_http_share_one_strict_replay(rewind, monkeypatch):
    calls = []
    app = combined_app(rewind, calls)

    incoming = EnvironBuilder(method="POST", path="/checkout", json={"amount": 4}).get_environ()
    statuses, body = serve(rewind.wsgi(app), incoming)
    assert statuses == ["503 SERVICE UNAVAILABLE"]
    assert b'"amount":6' in body
    snapshot = saved(rewind)
    assert snapshot.complete, snapshot.data["capture"]
    operations = {item["operation"] for item in snapshot.data["interactions"]}
    assert {"wsgi.read", "db.call", "http.request", "value"} <= operations

    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not reach a live database or HTTP transport")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(httpx.MockTransport, "handle_request", forbidden)
    assert rewind.replay_wsgi(snapshot, app).reproduced
    assert calls == ["/charge"]
    assert current.get() is None
    assert rewind.stats()["admitted"] == 1


def test_flask_combined_dependencies_replay_in_guarded_fresh_process(rewind):
    app = combined_app(rewind, [])
    incoming = EnvironBuilder(method="POST", path="/checkout", json={"amount": 4}).get_environ()
    serve(rewind.wsgi(app), incoming)
    snapshot = saved(rewind)
    path = rewind.store.path / f"{snapshot.id}.rewind.json"
    report = replay_file(path, "tests.test_sync_integration:combined_replay_target")
    assert report.reproduced, report
    assert report.isolation == "python-guard"


def test_wsgi_supports_input_iterable_with_distinct_iterator(rewind):
    class Input:
        def read(self, size=-1):
            return b"first\nsecond"

        def readline(self, size=-1):
            return b"first\n"

        def readlines(self, hint=-1):
            return [b"first\n", b"second"]

        def __iter__(self):
            return iter([b"first\n", b"second"])

    assert list(Input()) == [b"first\n", b"second"]

    def app(incoming, start):
        lines = list(incoming["wsgi.input"])
        start("200 OK", [("Content-Type", "text/plain")])
        return [b"".join(lines)]

    incoming = EnvironBuilder().get_environ()
    incoming["wsgi.input"] = Input()
    assert serve(rewind.wsgi(app), incoming)[1] == b"first\nsecond"
    assert rewind.replay_wsgi(saved(rewind), app).reproduced


def test_wsgi_caught_changed_database_query_stays_diverged(rewind, monkeypatch):
    changed = False

    def app(incoming, start):
        engine = create_engine()
        try:
            with engine.connect() as connection:
                connection.execute(text("select 2" if changed else "select 1")).scalar()
        except Exception:
            pass
        finally:
            engine.dispose()
        start("200 OK", [])
        return [b"same-output"]

    serve(rewind.wsgi(app), EnvironBuilder().get_environ())
    changed = True
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("live database"))
    assert rewind.replay_wsgi(saved(rewind), app).status == "diverged"


def test_wsgi_inputs_are_not_read_when_iteration_is_never_requested(rewind):
    class Input(io.BytesIO):
        def __iter__(self):
            pytest.fail("middleware must not request an unused input iterator")

    def app(incoming, start):
        start("200 OK", [])
        return [b"untouched"]

    incoming = EnvironBuilder().get_environ()
    incoming["wsgi.input"] = Input(b"unread")
    assert serve(rewind.wsgi(app), incoming)[1] == b"untouched"
    assert rewind.replay_wsgi(saved(rewind), app).reproduced


def test_wsgi_distinct_input_iterators_keep_their_own_position(rewind):
    class Input:
        def __iter__(self):
            return iter([b"one", b"two"])

    def app(incoming, start):
        left = iter(incoming["wsgi.input"])
        right = iter(incoming["wsgi.input"])
        result = next(left), next(right), list(left), list(right)
        assert result == (b"one", b"one", [b"two"], [b"two"])
        start("200 OK", [])
        return [b"ok"]

    incoming = EnvironBuilder().get_environ()
    incoming["wsgi.input"] = Input()
    assert serve(rewind.wsgi(app), incoming)[1] == b"ok"
    assert rewind.replay_wsgi(saved(rewind), app).reproduced


def test_wsgi_input_iterator_failure_preserves_application_and_marks_ineligible(rewind):
    class Input:
        def __iter__(self):
            raise ValueError("original input failure")

    def app(incoming, start):
        with pytest.raises(ValueError, match="original input failure"):
            iter(incoming["wsgi.input"])
        start("200 OK", [])
        return [b"fallback"]

    incoming = EnvironBuilder().get_environ()
    incoming["wsgi.input"] = Input()
    assert serve(rewind.wsgi(app), incoming)[1] == b"fallback"
    assert not saved(rewind).complete
    assert rewind.stats()["active_captures"] == 0


@pytest.mark.parametrize("stage", ["call", "iterate", "close"])
def test_wsgi_application_raised_divergence_remains_sticky(rewind, stage):
    from rewind.codecs import encode
    from rewind.snapshot import Snapshot

    def original(incoming, start):
        start("200 OK", [])
        return [b"ok"]

    serve(rewind.wsgi(original), EnvironBuilder().get_environ())
    # A developer-selected comparison oracle may be None. The exception must
    # not be converted into that successful outcome by replay error handling.
    document = saved(rewind).data
    document["outcome"] = {"kind": "return", "value": encode(None, rewind.limits)}

    class Response:
        def __iter__(self):
            if stage == "iterate":
                raise ReplayDivergence("direct application sentinel")
            yield b"ok"

        def close(self):
            if stage == "close":
                raise ReplayDivergence("direct application sentinel")

    def changed(incoming, start):
        if stage == "call":
            raise ReplayDivergence("direct application sentinel")
        start("200 OK", [])
        return Response()

    report = rewind.replay_wsgi(Snapshot.from_dict(document), changed)
    assert report.status == "diverged"
    assert report.detail == "application raised replay divergence"
    assert current.get() is None
