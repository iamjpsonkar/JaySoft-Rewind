import io
import sys

import pytest
from flask import Flask, request
from werkzeug.test import EnvironBuilder

from rewind import CapturePolicy, LocalStore, Retention, Rewind
from rewind.context import current


@pytest.fixture
def rewind(tmp_path):
    return Rewind(application="wsgi-tests", code_paths=[__file__],
                  store=LocalStore(tmp_path / "snapshots"), policy=CapturePolicy.synthetic(),
                  retain=Retention(always=True))


def snapshot(rewind):
    return rewind.store.load(rewind.store.ids()[-1])


def environ(body=b"one\ntwo", content_type="text/plain"):
    return EnvironBuilder(method="POST", path="/checkout", data=body,
                          content_type=content_type).get_environ()


def serve(app, incoming):
    response = []
    chunks = []

    def start_response(status, headers, exc_info=None):
        response.append((status, headers))
        return chunks.append

    result = app(incoming, start_response)
    try:
        chunks.extend(result)
    finally:
        close = getattr(result, "close", None)
        if close is not None:
            close()
    return response, chunks


def test_real_flask_request_body_http_response_and_replay(rewind):
    app = Flask(__name__)

    @app.post("/checkout")
    def checkout():
        body = request.get_json()
        return {"amount": body["amount"], "request_id": rewind.sources.uuid4()}, 503

    responses, chunks = serve(rewind.wsgi(app), environ(b'{"amount":4}', "application/json"))
    assert responses[0][0] == "503 SERVICE UNAVAILABLE"
    assert b'"amount":4' in b"".join(chunks)
    artifact = snapshot(rewind)
    assert artifact.complete, artifact.data["capture"]
    assert rewind.replay_wsgi(artifact, app).reproduced
    assert current.get() is None
    assert rewind.stats()["active_captures"] == 0


def test_wsgi_preserves_lazy_iteration_writes_close_and_request_reads(rewind):
    events = []

    class Response:
        def __iter__(self):
            events.append("iterate")
            yield b"one"
            events.append("second")
            yield b"two"

        def close(self):
            events.append("close")
            rewind.value("close-value", lambda: 3)

    def app(incoming, start):
        assert incoming["wsgi.input"].readline() == b"one\n"
        assert incoming["wsgi.input"].read(3) == b"two"
        write = start("200 OK", [("Content-Type", "text/plain")])
        write(b"prefix")
        return Response()

    output = []
    result = rewind.wsgi(app)(environ(), lambda *args: output.append)
    assert events == [] and output == [b"prefix"]
    assert current.get() is None
    assert next(result) == b"one"
    assert events == ["iterate"]
    assert list(result) == [b"two"]
    assert not rewind.store.ids()
    result.close()
    assert events == ["iterate", "second", "close"]
    assert rewind.replay_wsgi(snapshot(rewind), app).reproduced


def test_wsgi_changed_read_fails_without_live_fallback(rewind):
    def app(incoming, start):
        incoming["wsgi.input"].read(3)
        start("200 OK", [])
        return [b"ok"]

    serve(rewind.wsgi(app), environ())

    def changed(incoming, start):
        try:
            incoming["wsgi.input"].read(4)
        except Exception:
            pass
        start("200 OK", [])
        return [b"ok"]

    assert rewind.replay_wsgi(snapshot(rewind), changed).status == "diverged"


def test_wsgi_early_close_is_ineligible_and_does_not_drain(rewind):
    events = []

    def app(incoming, start):
        start("200 OK", [])
        try:
            events.append(1)
            yield b"first"
            events.append(2)
            yield b"second"
        finally:
            events.append("close")

    response = rewind.wsgi(app)(environ(), lambda *args: lambda body: None)
    assert next(response) == b"first"
    response.close()
    assert events == [1, "close"]
    assert not snapshot(rewind).complete
    assert rewind.stats()["active_captures"] == 0


@pytest.mark.parametrize("stage", ["iterate", "close"])
def test_wsgi_application_errors_are_preserved_and_replayable(rewind, stage):
    class Response:
        def __iter__(self):
            yield b"first"
            if stage == "iterate":
                raise ValueError("iteration error")

        def close(self):
            if stage == "close":
                raise ValueError("close error")

    def app(incoming, start):
        start("200 OK", [])
        return Response()

    with pytest.raises(ValueError, match="error"):
        serve(rewind.wsgi(app), environ())
    assert current.get() is None
    assert rewind.stats()["active_captures"] == 0
    assert rewind.replay_wsgi(snapshot(rewind), app).reproduced


def test_wsgi_request_secrets_are_redacted(rewind):
    def app(incoming, start):
        start("200 OK", [])
        return [b"ok"]

    incoming = environ()
    incoming["HTTP_AUTHORIZATION"] = "header-private"
    incoming["QUERY_STRING"] = "token=query-private"
    serve(rewind.wsgi(app), incoming)
    artifact = snapshot(rewind)
    assert not artifact.complete
    assert b"header-private" not in artifact.raw
    assert b"query-private" not in artifact.raw


def test_wsgi_no_eager_input_consumption_and_disabled_passthrough(rewind):
    class Input(io.BytesIO):
        def read(self, *args):
            pytest.fail("application never reads the request")

    def app(incoming, start):
        start("200 OK", [])
        return [b"ok"]

    incoming = environ()
    incoming["wsgi.input"] = Input(b"untouched")
    assert serve(rewind.wsgi(app), incoming)[1] == [b"ok"]
    assert rewind.replay_wsgi(snapshot(rewind), app).reproduced
    rewind.disable()
    result = rewind.wsgi(app)(incoming, lambda *args: lambda body: None)
    assert type(result) is list


def test_wsgi_request_readinto_and_iteration_preserve_values(rewind):
    def app(incoming, start):
        stream = incoming["wsgi.input"]
        target = bytearray(4)
        assert stream.readinto(target) == 4
        assert target == b"one\n"
        assert list(stream) == [b"two"]
        start("200 OK", [])
        return [b"ok"]

    serve(rewind.wsgi(app), environ())
    assert rewind.replay_wsgi(snapshot(rewind), app).reproduced


def test_wsgi_readinto_fallback_for_minimal_wsgi_stream(rewind):
    class Input:
        def read(self, size):
            assert size == 3
            return b"abc"

    def app(incoming, start):
        target = bytearray(3)
        assert incoming["wsgi.input"].readinto(target) == 3
        assert target == b"abc"
        start("200 OK", [])
        return [b"ok"]

    incoming = environ()
    incoming["wsgi.input"] = Input()
    serve(rewind.wsgi(app), incoming)
    assert rewind.replay_wsgi(snapshot(rewind), app).reproduced


def test_wsgi_read_arguments_and_input_exceptions_replay(rewind):
    class Input(io.BytesIO):
        def read(self, size=-1):
            if size == 999:
                raise OSError("request stream failed")
            return super().read(size)

    def app(incoming, start):
        assert incoming["wsgi.input"].read(size=2) == b"ab"
        try:
            incoming["wsgi.input"].read(999)
        except OSError:
            pass
        start("200 OK", [])
        return [b"ok"]

    incoming = environ()
    incoming["wsgi.input"] = Input(b"abc")
    serve(rewind.wsgi(app), incoming)
    assert rewind.replay_wsgi(snapshot(rewind), app).reproduced


def test_wsgi_start_response_exc_info_is_forwarded_unchanged(rewind):
    observed = []
    expected = []

    def app(incoming, start):
        try:
            raise ValueError("original")
        except ValueError:
            expected.append(sys.exc_info())
            start("500 ERROR", [], expected[-1])
        return [b"failure"]

    def downstream(status, headers, exc_info):
        observed.append(exc_info)
        return lambda body: None

    response = rewind.wsgi(app)(environ(), downstream)
    assert list(response) == [b"failure"]
    response.close()
    assert observed[0] is expected[0]
    assert rewind.replay_wsgi(snapshot(rewind), app).reproduced


def test_wsgi_server_write_error_does_not_become_false_replay_promise(rewind):
    def app(incoming, start):
        write = start("200 OK", [])
        try:
            write(b"response")
        except OSError:
            return [b"fallback"]

    def write(body):
        raise OSError("server gone")

    response = rewind.wsgi(app)(environ(), lambda *args: write)
    assert list(response) == [b"fallback"]
    response.close()
    assert not snapshot(rewind).complete


def test_wsgi_finalization_failure_is_fail_open_and_releases_reservation(rewind, monkeypatch):
    def app(incoming, start):
        start("not-a-status", [])
        return [b"actual-response"]

    assert serve(rewind.wsgi(app), environ())[1] == [b"actual-response"]
    assert not snapshot(rewind).complete
    assert rewind.stats()["active_captures"] == 0


def test_wsgi_late_callbacks_do_not_retain_more_capture_payload(rewind):
    callbacks = []

    def app(incoming, start):
        callbacks.append(incoming["wsgi.input"])
        callbacks.append(start("200 OK", [("X-Header", "original")]))
        return [b"response"]

    writes = []
    response = rewind.wsgi(app)(environ(), lambda *args: writes.append)
    assert list(response) == [b"response"]
    response.close()
    assert response.exchange.headers == response.exchange.chunks == []
    assert callbacks[0].read() == b"one\ntwo"
    callbacks[1](b"late-output")
    assert writes == [b"late-output"]
    assert response.exchange.headers == response.exchange.chunks == []
    assert rewind.stats()["retained"] == 1


def test_wsgi_body_budget_does_not_change_delivered_output(tmp_path):
    from rewind import Limits

    rewind = Rewind(application="limits", code_paths=[__file__],
                    store=LocalStore(tmp_path / "small"), policy=CapturePolicy.synthetic(),
                    retain=Retention(always=True), limits=Limits(body_bytes=4))

    def app(incoming, start):
        start("200 OK", [])
        return [b"123", b"456"]

    assert serve(rewind.wsgi(app), environ())[1] == [b"123", b"456"]
    assert not snapshot(rewind).complete
    assert rewind.stats()["active_captures"] == 0
