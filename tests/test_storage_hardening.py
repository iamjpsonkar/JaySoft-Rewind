import multiprocessing
import os
import stat
from pathlib import Path

import pytest

from rewind import LocalStore
from rewind.errors import InvalidSnapshot, RewindError
from rewind.snapshot import Snapshot
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


def _concurrent_writer(path, raw, max_bytes, barrier):
    store = LocalStore(path, max_bytes=max_bytes)
    barrier.wait(timeout=10)
    store.save(Snapshot.from_bytes(raw))


def _hold_directory_lock(path, ready, release):
    store = LocalStore(path)
    with store._locked():
        ready.set()
        assert release.wait(10)


def _observe_locked_writer(path, raw, attempting, completed):
    import fcntl

    original = fcntl.flock

    def observe(fd, operation):
        if operation == fcntl.LOCK_EX:
            attempting.set()
        return original(fd, operation)

    fcntl.flock = observe
    store = LocalStore(path)
    store.save(Snapshot.from_bytes(raw))
    completed.set()


@pytest.mark.skipif(os.name != "posix", reason="advisory directory locks require POSIX")
async def test_distinct_processes_serialize_directory_publication(recorder, tmp_path):
    first, _ = await snapshots(recorder)
    context = multiprocessing.get_context("spawn")
    ready, release, attempting, completed = (context.Event() for _ in range(4))
    path = tmp_path / "shared"
    holder = context.Process(target=_hold_directory_lock, args=(path, ready, release))
    writer = context.Process(target=_observe_locked_writer,
                             args=(path, first.raw, attempting, completed))
    holder.start()
    try:
        assert ready.wait(10)
        writer.start()
        assert attempting.wait(10)
        assert not completed.is_set()
        release.set()
        assert completed.wait(10)
    finally:
        release.set()
        holder.join(10)
        if writer.pid is not None:
            writer.join(10)
        for process in (holder, writer):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)
    assert holder.exitcode == writer.exitcode == 0
    assert LocalStore(path).load(first.id) == first


@pytest.mark.skipif(os.name != "posix", reason="advisory directory locks require POSIX")
async def test_concurrent_process_quota_is_applied_to_shared_artifacts(recorder, tmp_path):
    first, _ = await snapshots(recorder)
    artifacts = []
    for index in range(4):
        document = first.data
        document["snapshot_id"] = f"{index:032x}"
        artifacts.append(Snapshot.from_dict(document))
    maximum = max(len(artifact.raw) for artifact in artifacts)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(artifacts))
    path = tmp_path / "shared"
    processes = [context.Process(target=_concurrent_writer,
                                 args=(path, artifact.raw, maximum, barrier))
                 for artifact in artifacts]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
        assert all(process.exitcode == 0 for process in processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
    store = LocalStore(path, max_bytes=maximum)
    assert len(store.ids()) == 1
    assert sum(item.stat().st_size for item in path.glob("*.rewind.json")) <= maximum
    assert store.load(store.ids()[0]).id in {artifact.id for artifact in artifacts}


async def test_prune_applies_ttl_without_another_capture(recorder, tmp_path):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "prune", retention_seconds=30)
    old = store.save(first)
    store.save(second)
    os.utime(old, (1, 1))
    report = store.prune()
    assert report == {"removed": 1, "removed_bytes": len(first.raw),
                      "remaining_items": 1, "remaining_bytes": len(second.raw)}
    assert store.ids() == [second.id]
    assert store.prune()["removed"] == 0


async def test_prune_applies_lowered_quota_in_oldest_order(recorder, tmp_path):
    first, second = await snapshots(recorder)
    path = tmp_path / "prune"
    original = LocalStore(path)
    original.save(first)
    original.save(second)
    older = original._path(second.id).stat().st_mtime - 1
    os.utime(original._path(first.id), (older, older))
    smaller = LocalStore(path, max_bytes=max(len(first.raw), len(second.raw)))
    assert smaller.prune()["removed"] == 1
    assert smaller.ids() == [second.id]


@pytest.mark.skipif(os.name != "posix", reason="directory fsync requires POSIX")
async def test_directory_synced_before_eviction_and_after_metadata_updates(
    recorder, tmp_path, monkeypatch
):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "sync", max_bytes=max(len(first.raw), len(second.raw)))
    store.save(first)
    events = []
    fsync, replace, unlink = os.fsync, os.replace, os.unlink

    def sync(fd):
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return fsync(fd)

    def publish(source, target):
        events.append("publish")
        return replace(source, target)

    def remove(path, *args, **kwargs):
        if str(path).endswith(f"{first.id}.rewind.json"):
            events.append("evict")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", publish)
    monkeypatch.setattr(os, "unlink", remove)
    store.save(second)
    assert events.index("file") < events.index("publish") < events.index("directory")
    assert events.index("directory") < events.index("evict") < len(events) - 1
    assert events[-1] == "directory"


@pytest.mark.skipif(os.name != "posix", reason="directory fsync requires POSIX")
async def test_failed_publication_directory_sync_preserves_old_evidence(
    recorder, tmp_path, monkeypatch
):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "sync", max_bytes=max(len(first.raw), len(second.raw)))
    store.save(first)
    original = os.fsync
    failed = False

    def fail_directory_once(fd):
        nonlocal failed
        if stat.S_ISDIR(os.fstat(fd).st_mode) and not failed:
            failed = True
            raise OSError("directory metadata sync failed")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_once)
    with pytest.raises(OSError, match="metadata sync"):
        store.save(second)
    assert store.load(first.id) == first
    assert store.load(second.id) == second  # Publication happened; durability was not confirmed.
    assert not list(store.path.glob(".pending-*"))


async def test_mismatched_filename_identity_rejected(recorder, tmp_path):
    first, second = await snapshots(recorder)
    store = LocalStore(tmp_path / "identity")
    store.save(first).rename(store._path(second.id))
    with pytest.raises(InvalidSnapshot, match="storage name"):
        store.load(second.id)
