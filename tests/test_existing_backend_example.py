import httpx

from examples import existing_backend
from rewind import LocalStore, replay_file


def test_existing_backend_records_response_without_credentials(tmp_path, monkeypatch):
    calls = []
    client = httpx.Client

    def make_client(**kwargs):
        def respond(request):
            calls.append(request)
            assert request.headers["authorization"] == "Bearer local-fixture-secret"
            return httpx.Response(503, json={"error": "fixture service unavailable"})

        return client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "Client", make_client)
    summary = existing_backend.record(
        "http://localhost:8001/fixture",
        tmp_path / "snapshots",
        {"authorization": "Bearer local-fixture-secret"},
    )
    assert summary["http_status"] == 503
    assert summary["complete"]
    store = LocalStore(tmp_path / "snapshots")
    snapshot = store.load(store.ids()[0])
    assert b"local-fixture-secret" not in snapshot.raw
    target = existing_backend.make_target()
    assert target.rewind.replay_sync(snapshot, target.entrypoint).reproduced
    assert len(calls) == 1
    report = replay_file(summary["snapshot"], "examples.existing_backend:replay_target")
    assert report.reproduced, report.to_dict()
    assert report.isolation == "python-guard"
