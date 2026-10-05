import pytest

from examples import combined_failure
from rewind import replay_file
from rewind.storage import load_file


async def test_combined_snapshot_replays_every_boundary_without_live_access(tmp_path, monkeypatch):
    artifact = await combined_failure.record(tmp_path / "recordings")
    snapshot = load_file(artifact)
    assert snapshot.complete
    assert {i["operation"] for i in snapshot.data["interactions"]} == {
        "db.call",
        "redis.command",
        "value",
        "http.request",
    }

    def forbidden(*args, **kwargs):
        pytest.fail("live dependency called during replay")

    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr(combined_failure.FixtureCache, "get", forbidden)
    monkeypatch.setattr("httpx.MockTransport.handle_async_request", forbidden)
    target = combined_failure.replay_target()
    report = await target.rewind.replay(snapshot, target.entrypoint)
    assert report.reproduced
    assert report.consumed == report.total


async def test_combined_snapshot_replays_in_fresh_process(tmp_path):
    artifact = await combined_failure.record(tmp_path / "recordings")
    report = replay_file(artifact, "examples.combined_failure:replay_target")
    assert report.reproduced, report.to_dict()
