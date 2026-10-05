import os
from pathlib import Path

import pytest

from rewind import LocalStore
from rewind.errors import InvalidSnapshot, RewindError
from rewind.storage import load_file


async def snapshots(recorder):
    async def operation(value):
        return value

    await recorder.run(operation, 1)
    await recorder.run(operation, 2)
    return [recorder.store.load(key) for key in recorder.store.ids()]


async def test_overwriting_snapshot_does_not_evict_unrelated_artifact(recorder, tmp_path):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "quota", max_bytes=len(first.raw) + len(second.raw))
    store.save(first)
    store.save(second)
    store.save(second)
    assert store.ids() == sorted([first.id, second.id])


@pytest.mark.parametrize("failure", ["fsync", "replace"])
async def test_failed_publication_preserves_existing_artifacts(
    recorder, tmp_path, monkeypatch, failure
):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "quota", max_bytes=max(len(first.raw), len(second.raw)))
    original = store.save(first)

    def fail(*args):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(os, failure, fail)
    with pytest.raises(OSError, match="simulated"):
        store.save(second)
    assert store.load(first.id).raw == first.raw
    assert list(store.path.iterdir()) == [original]


async def test_quota_evicts_oldest_after_successful_publication(recorder, tmp_path):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "quota", max_bytes=max(len(first.raw), len(second.raw)))
    store.save(first)
    store.save(second)
    assert store.ids() == [second.id]
    assert store.load(second.id).raw == second.raw


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
def test_loading_fifo_fails_without_waiting_for_writer(tmp_path):
    path = tmp_path / "artifact"
    os.mkfifo(path)
    with pytest.raises(InvalidSnapshot, match="regular file"):
        load_file(path)


async def test_nonregular_store_entry_rejected_before_eviction(recorder, tmp_path):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "quota", max_bytes=max(len(first.raw), len(second.raw)))
    store.save(first)
    impostor = store.path / ("f" * 32 + ".rewind.json")
    impostor.mkdir()
    with pytest.raises(RewindError, match="non-regular"):
        store.save(second)
    assert store.load(first.id).raw == first.raw


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), True, 0])
def test_invalid_retention_rejected(tmp_path: Path, duration):
    with pytest.raises(ValueError):
        LocalStore(tmp_path / "store", retention_seconds=duration)
