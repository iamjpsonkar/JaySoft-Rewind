import json
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest

from rewind.context import current
from rewind.persistence import BackgroundWriter
from rewind.snapshot import Snapshot


@pytest.fixture
async def snapshot(recorder):
    async def operation():
        return {"result": "synthetic"}

    await recorder.run(operation)
    return recorder.store.load(recorder.store.ids()[0])


class BlockedStore:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.saved = []

    def save(self, snapshot):
        self.started.set()
        assert self.release.wait(5), "test did not release blocked save"
        self.saved.append(snapshot)


@pytest.fixture
def blocked_writer():
    store = BlockedStore()
    writer = BackgroundWriter(store, max_items=3)
    try:
        yield writer, store
    finally:
        store.release.set()
        writer.close()


def test_count_budget_includes_inflight_and_submit_does_not_wait(snapshot, blocked_writer):
    writer, store = blocked_writer
    assert writer.submit(snapshot)
    assert store.started.wait(5)
    assert writer.submit(snapshot)
    assert writer.submit(snapshot)
    assert not writer.submit(snapshot)
    stats = writer.stats()
    assert stats["pending_items"] == 3
    assert stats["pending_bytes"] == 3 * len(snapshot.raw)
    assert stats["submitted"] == 3
    assert stats["rejected"] == 1
    assert stats["in_flight"]
    assert stats["worker_alive"]
    assert stats["max_items"] == stats["peak_pending_items"] == 3
    assert stats["peak_pending_bytes"] == 3 * len(snapshot.raw)
    assert not writer.flush(0)
    store.release.set()
    assert writer.flush()
    assert writer.stats()["saved"] == 3
    assert writer.stats()["pending_bytes"] == 0
    assert writer.stats()["peak_pending_bytes"] == 3 * len(snapshot.raw)


def test_byte_budget_exact_boundary_and_oversized_snapshot(snapshot):
    store = BlockedStore()
    writer = BackgroundWriter(store, max_bytes=len(snapshot.raw), max_items=100)
    try:
        assert not writer.submit(Snapshot(snapshot.raw + b" "))
        assert writer.submit(snapshot)
        assert store.started.wait(5)
        assert not writer.submit(snapshot)
        assert writer.stats()["pending_bytes"] == len(snapshot.raw)
        store.release.set()
        assert writer.flush()
        assert writer.submit(snapshot)
        assert writer.flush()
        assert writer.stats()["saved"] == 2
    finally:
        store.release.set()
        writer.close()


def test_concurrent_producers_obey_shared_capacity(snapshot, blocked_writer):
    writer, store = blocked_writer
    assert writer.submit(snapshot)
    assert store.started.wait(5)
    with ThreadPoolExecutor(max_workers=12) as producers:
        admitted = list(producers.map(lambda _: writer.submit(snapshot), range(100)))
    assert sum(admitted) == 2
    assert writer.stats()["pending_items"] == 3
    assert writer.stats()["rejected"] == 98
    store.release.set()
    assert writer.flush()
    assert len(store.saved) == 3


@pytest.mark.parametrize("failure", [OSError, SystemExit])
def test_store_failure_is_private_and_worker_recovers(snapshot, failure, capsys):
    class Store:
        calls = 0

        def save(self, snapshot):
            self.calls += 1
            if self.calls == 1:
                raise failure("sensitive-store-secret")

    writer = BackgroundWriter(Store())
    try:
        assert writer.submit(snapshot)
        assert writer.flush()
        assert writer.submit(snapshot)
        assert writer.flush()
        stats = writer.stats()
        assert stats["saved"] == stats["failed"] == 1
        assert stats["pending_items"] == stats["pending_bytes"] == 0
        assert all(type(value) in (int, bool) for value in stats.values())
        assert "sensitive-store-secret" not in json.dumps(stats)
        assert capsys.readouterr() == ("", "")
    finally:
        writer.close()


@pytest.mark.parametrize("drain", [False, True])
def test_shutdown_drops_pending_but_reports_uncancellable_inflight(
    snapshot, blocked_writer, drain
):
    writer, store = blocked_writer
    assert writer.submit(snapshot)
    assert store.started.wait(5)
    assert writer.submit(snapshot)
    assert writer.submit(snapshot)
    report = writer.close(timeout=0, drain=drain)
    assert not report.drained
    assert report.pending == 1
    assert report.in_flight
    assert report.worker_alive
    assert report.dropped == 2
    assert writer.stats()["pending_bytes"] == len(snapshot.raw)
    assert not writer.submit(snapshot)
    assert writer.close(timeout=0).dropped == 2
    store.release.set()
    report = writer.close()
    assert not report.worker_alive
    assert not report.in_flight
    assert report.pending == 0
    assert report.dropped == 2
    assert not report.drained
    assert store.saved == [snapshot]


def test_graceful_close_is_idempotent_and_rejects_future_submissions(snapshot):
    class Store:
        saved = []

        def save(self, snapshot):
            self.saved.append(snapshot)

    store = Store()
    writer = BackgroundWriter(store)
    assert writer.submit(snapshot)
    report = writer.close()
    assert report.drained
    assert report.pending == report.dropped == 0
    assert not report.worker_alive
    assert store.saved == [snapshot]
    assert not writer.submit(snapshot)
    assert writer.close() == report
    assert writer.flush(0)


def test_snapshot_bytes_are_only_payload_accepted(snapshot, blocked_writer):
    writer, _ = blocked_writer
    assert not writer.submit(Snapshot(bytearray(snapshot.raw)))
    assert not writer.submit(snapshot.data)
    assert not writer.submit(object())
    assert writer.stats()["pending_items"] == 0
    assert writer.stats()["rejected"] == 3


def test_worker_does_not_inherit_capture_context(snapshot):
    marker = ContextVar("private_request", default=None)
    observed = []

    class Store:
        def save(self, snapshot):
            observed.append((current.get(), marker.get()))

    capture_token = current.set("active-capture")
    marker_token = marker.set("private-request")
    writer = BackgroundWriter(Store())
    try:
        assert writer.submit(snapshot)
        assert writer.flush()
        assert observed == [(None, None)]
    finally:
        writer.close()
        current.reset(capture_token)
        marker.reset(marker_token)


def test_fork_detection_avoids_inherited_locks(snapshot, blocked_writer, monkeypatch):
    writer, _ = blocked_writer

    class ForbiddenLock:
        def __enter__(self):
            pytest.fail("child touched inherited condition")

    with monkeypatch.context() as patch:
        patch.setattr("rewind.persistence.os.getpid", lambda: -1)
        patch.setattr(writer, "_condition", ForbiddenLock())
        assert not writer.submit(snapshot)
        assert not writer.flush()
        assert writer.stats()["forked"]
        report = writer.close()
        assert report.forked
        assert not report.drained
        assert not report.worker_alive


@pytest.mark.parametrize("capacity", [0, -1, True, 1.2, "2", None])
@pytest.mark.parametrize("field", ["max_items", "max_bytes"])
def test_invalid_capacity_fails_before_starting_worker(capacity, field):
    with pytest.raises(ValueError, match="capacities"):
        BackgroundWriter(BlockedStore(), **{field: capacity})


@pytest.mark.parametrize(
    "timeout", [-1, float("inf"), float("nan"), True, None, "5", 10**1000]
)
def test_invalid_timeouts_do_not_close_writer(blocked_writer, timeout):
    writer, _ = blocked_writer
    with pytest.raises(ValueError, match="timeout"):
        writer.flush(timeout)
    with pytest.raises(ValueError, match="timeout"):
        writer.close(timeout)
    assert not writer.stats()["closed"]


def test_invalid_drain_flag_is_rejected(blocked_writer):
    writer, _ = blocked_writer
    with pytest.raises(ValueError, match="drain"):
        writer.close(drain=1)
    assert not writer.stats()["closed"]


def test_local_store_round_trip(snapshot, tmp_path):
    from rewind.storage import LocalStore

    store = LocalStore(tmp_path / "queued")
    writer = BackgroundWriter(store)
    assert writer.submit(snapshot)
    assert writer.close().drained
    assert store.load(snapshot.id) == snapshot


def test_shutdown_releases_dropped_snapshot_objects(snapshot, blocked_writer):
    writer, store = blocked_writer
    assert writer.submit(snapshot)
    assert store.started.wait(5)
    dropped = Snapshot(snapshot.raw)
    reference = weakref.ref(dropped)
    assert writer.submit(dropped)
    del dropped
    assert reference() is not None
    assert writer.close(timeout=0).dropped == 1
    assert reference() is None


def test_concurrent_shutdown_accounts_for_each_discard_once(snapshot, blocked_writer):
    writer, store = blocked_writer
    assert writer.submit(snapshot)
    assert store.started.wait(5)
    assert writer.submit(snapshot)
    assert writer.submit(snapshot)
    with ThreadPoolExecutor(max_workers=8) as callers:
        reports = list(callers.map(lambda _: writer.close(timeout=0), range(20)))
    assert all(report.pending == 1 and report.dropped == 2 for report in reports)
    assert writer.stats()["submitted"] == 3
    assert writer.stats()["dropped"] == 2
    store.release.set()
    assert writer.close().pending == 0
    assert writer.stats()["saved"] == 1


def test_flush_waits_for_concurrently_submitted_work(snapshot, monkeypatch):
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()
    second_release = threading.Event()
    flush_waiting = threading.Event()
    flush_waiting_again = threading.Event()

    class Store:
        calls = 0

        def save(self, snapshot):
            self.calls += 1
            if self.calls == 1:
                first_started.set()
                assert first_release.wait(5)
            else:
                second_started.set()
                assert second_release.wait(5)

    writer = BackgroundWriter(Store())
    original_wait = writer._condition.wait
    wait_count = 0

    def signal_wait(timeout=None):
        nonlocal wait_count
        if threading.current_thread().name.startswith("flush-test"):
            wait_count += 1
            (flush_waiting if wait_count == 1 else flush_waiting_again).set()
        return original_wait(timeout)

    monkeypatch.setattr(writer._condition, "wait", signal_wait)
    try:
        assert writer.submit(snapshot)
        assert first_started.wait(5)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="flush-test") as caller:
            future = caller.submit(writer.flush)
            try:
                assert flush_waiting.wait(5)
                assert writer.submit(snapshot)
                first_release.set()
                assert second_started.wait(5)
                assert flush_waiting_again.wait(5)
                assert not future.done()
            finally:
                first_release.set()
                second_release.set()
            assert future.result(timeout=5)
        assert writer.stats()["saved"] == 2
    finally:
        first_release.set()
        second_release.set()
        writer.close()
