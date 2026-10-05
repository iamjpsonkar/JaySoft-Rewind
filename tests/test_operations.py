"""Operational behavior under queue saturation, cancellation, and shutdown."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from rewind import CapturePolicy, Limits, LocalStore, Retention, Rewind
from rewind.context import current
from rewind.persistence import BackgroundWriter


class GateStore:
    def __init__(self, store, *, fail=False):
        self.store = store
        self.fail = fail
        self.entered = threading.Event()
        self.release = threading.Event()

    def save(self, snapshot):
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("test persistence gate timed out")
        if self.fail:
            raise OSError("sensitive error must not appear in counters")
        return self.store.save(snapshot)


def make_rewind(**kwargs):
    return Rewind(
        application="operations-test", code_paths=[__file__],
        policy=CapturePolicy.synthetic(), retain=Retention(always=True), **kwargs,
    )


async def wait_thread(event):
    assert await asyncio.to_thread(event.wait, 2)


async def test_queued_does_not_mean_persisted_and_replays(tmp_path):
    store = LocalStore(tmp_path / "artifacts")
    gate = GateStore(store)
    writer = BackgroundWriter(gate)
    rewind = make_rewind(writer=writer)

    async def operation():
        return {"observation": rewind.sources.uuid4()}

    try:
        await rewind.run(operation)
        await wait_thread(gate.entered)
        stats = rewind.stats()
        assert stats["submitted"] == stats["retained"] == 1
        assert stats["persisted"] == 0
        assert stats["pending_items"] == 1
        assert stats["active_captures"] == stats["reserved_bytes"] == 0
        stats["admitted"] = 999
        assert rewind.stats()["admitted"] == 1
        gate.release.set()
        assert await asyncio.to_thread(writer.flush, 2)
        assert rewind.stats()["persisted"] == rewind.metrics["persisted"] == 1
        assert (await rewind.replay(store.load(store.ids()[0]), operation)).reproduced
    finally:
        gate.release.set()
        await rewind.aclose()


async def test_failure_storm_drops_without_changing_application_errors(tmp_path):
    gate = GateStore(LocalStore(tmp_path / "artifacts"), fail=True)
    writer = BackgroundWriter(gate, max_items=1)
    rewind = make_rewind(writer=writer)

    async def operation():
        raise ValueError("application failure")

    try:
        for _ in range(30):
            with pytest.raises(ValueError, match="application failure"):
                await rewind.run(operation)
        await wait_thread(gate.entered)
        stats = rewind.stats()
        assert stats["admitted"] == stats["retained"] == 30
        assert stats["submitted"] == 1
        assert stats["enqueue_rejected"] == 29
        assert stats["persisted"] == stats["persistence_failed"] == 0
        gate.release.set()
        assert await asyncio.to_thread(writer.flush, 2)
        stats = rewind.stats()
        assert stats["persistence_failed"] == 1
        assert stats["active_captures"] == stats["reserved_bytes"] == 0
        assert "sensitive" not in str(stats)
    finally:
        gate.release.set()
        await rewind.aclose()


async def test_disable_preserves_active_capture_then_enable_resumes(tmp_path):
    store = LocalStore(tmp_path / "artifacts")
    rewind = make_rewind(store=store)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()
        return "original"

    async def quick():
        return "uncaptured"

    task = asyncio.create_task(rewind.run(slow))
    await entered.wait()
    rewind.disable()
    assert not rewind.enabled
    assert await rewind.run(quick) == "uncaptured"
    release.set()
    assert await task == "original"
    assert rewind.stats()["persisted"] == 1
    rewind.enabled = True
    await rewind.run(quick)
    assert rewind.stats()["persisted"] == 2


async def test_close_rejects_active_artifact_and_releases_payload(tmp_path):
    rewind = make_rewind(writer=BackgroundWriter(LocalStore(tmp_path / "artifacts")))
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def operation(payload):
        observed.append(current.get())
        rewind.sources.uuid4()
        entered.set()
        await release.wait()
        return payload

    task = asyncio.create_task(rewind.run(operation, {"value": "payload"}))
    await entered.wait()
    assert rewind.stats()["active_captures"] == 1
    assert rewind.stats()["reserved_bytes"] == rewind.limits.snapshot_bytes
    report = await rewind.aclose(timeout=0.5)
    assert not report.worker_alive
    assert rewind.stats()["active_captures"] == 1
    with pytest.raises(RuntimeError, match="closed"):
        rewind.enable()
    release.set()
    assert await task == {"value": "payload"}
    assert rewind.stats()["closed_rejected"] == 1
    assert rewind.stats()["pending_items"] == rewind.stats()["reserved_bytes"] == 0
    recorder = observed[0]
    assert recorder.sealed and recorder.interactions == [] and recorder.bytes_used == 0
    assert "payload" not in str(recorder.input)


async def test_cancellation_is_preserved_and_reservation_released(tmp_path):
    store = LocalStore(tmp_path / "artifacts")
    rewind = make_rewind(writer=BackgroundWriter(store))
    entered = asyncio.Event()

    async def operation():
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(rewind.run(operation))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert current.get() is None
    assert rewind.stats()["active_captures"] == 0
    assert (await rewind.aclose()).drained
    snapshot = store.load(store.ids()[0])
    assert not snapshot.complete
    assert "execution_interrupted" in snapshot.data["capture"]["ineligible_reasons"]


async def test_background_asgi_response_and_disable_are_transparent(tmp_path):
    store = LocalStore(tmp_path / "artifacts")
    rewind = make_rewind(writer=BackgroundWriter(store))

    async def app(scope, receive, send):
        body = bytearray()
        while True:
            incoming = await receive()
            body.extend(incoming.get("body", b""))
            if not incoming.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": bytes(body)})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rewind.asgi(app)), base_url="http://test"
    ) as client:
        response = await client.post("/", content=b"synthetic")
        assert response.status_code == 503 and response.content == b"synthetic"
        rewind.disable()
        response = await client.post("/", content=b"still-live")
        assert response.content == b"still-live"
    assert (await rewind.aclose()).drained
    assert rewind.stats()["admitted"] == rewind.stats()["persisted"] == 1
    assert (await rewind.replay_asgi(store.load(store.ids()[0]), app)).reproduced


async def test_admission_budget_and_thread_safe_stats(tmp_path):
    rewind = make_rewind(
        store=LocalStore(tmp_path / "artifacts"),
        limits=Limits(snapshot_bytes=65536, active_bytes=65536),
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()

    async def quick():
        return 4

    task = asyncio.create_task(rewind.run(slow))
    await entered.wait()
    assert await rewind.run(quick) == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        samples = list(pool.map(lambda _: rewind.stats(), range(200)))
    assert all(sample["active_captures"] == 1 for sample in samples)
    assert all(sample["admission_rejected"] == 1 for sample in samples)
    release.set()
    await task
    assert rewind.stats()["reserved_bytes"] == 0


async def test_close_is_bounded_and_does_not_block_event_loop(tmp_path):
    gate = GateStore(LocalStore(tmp_path / "artifacts"))
    rewind = make_rewind(writer=BackgroundWriter(gate))

    async def operation():
        return "ok"

    try:
        await rewind.run(operation)
        await wait_thread(gate.entered)
        # A zero timeout reports the blocked write without waiting for it.
        report = rewind.close(timeout=0)
        assert report.worker_alive and report.pending == 1 and not report.drained
        closing = asyncio.create_task(rewind.aclose(timeout=2))
        marker = asyncio.Event()
        asyncio.get_running_loop().call_soon(marker.set)
        await asyncio.wait_for(marker.wait(), timeout=1)
        assert not closing.done()
        gate.release.set()
        assert (await closing).drained
        assert await rewind.run(operation) == "ok"
        assert rewind.stats()["admitted"] == 1
    finally:
        gate.release.set()
        await rewind.aclose(timeout=2)


async def test_closed_writer_and_submission_failure_preserve_result(tmp_path, monkeypatch):
    writer = BackgroundWriter(LocalStore(tmp_path / "artifacts"))
    rewind = make_rewind(writer=writer)
    writer.close()

    async def operation():
        return "application-result"

    assert await rewind.run(operation) == "application-result"
    assert rewind.stats()["enqueue_rejected"] == 1

    def fail(snapshot):
        raise OSError("sensitive writer details")

    monkeypatch.setattr(writer, "submit", fail)
    assert await rewind.run(operation) == "application-result"
    assert rewind.metrics["persistence_failed"] == 1
    assert rewind.stats()["active_captures"] == 0
    assert "sensitive" not in str(rewind.stats())


def test_mutually_exclusive_persistence_and_invalid_close(tmp_path):
    store = LocalStore(tmp_path / "artifacts")
    writer = BackgroundWriter(store)
    try:
        with pytest.raises(ValueError, match="mutually exclusive"):
            make_rewind(store=store, writer=writer)
        rewind = make_rewind()
        for timeout in [-1, float("inf"), float("nan"), None]:
            with pytest.raises(ValueError):
                rewind.close(timeout)
        assert rewind.enabled and not rewind.stats()["closed"]
        assert rewind.close().drained
        assert rewind.close().drained
    finally:
        writer.close()


@pytest.mark.parametrize("background", [False, True])
async def test_invalid_shutdown_arguments_leave_admission_open(tmp_path, background):
    writer = BackgroundWriter(LocalStore(tmp_path / "artifacts")) if background else None
    rewind = make_rewind(writer=writer)
    try:
        for timeout in [False, -1, float("nan"), float("inf"), 10**1000]:
            with pytest.raises(ValueError):
                rewind.close(timeout)
            with pytest.raises(ValueError):
                await rewind.aclose(timeout)
            with pytest.raises(ValueError):
                await rewind.aflush(timeout)
        for drain in [1, 0, None, "false"]:
            with pytest.raises(ValueError):
                rewind.close(drain=drain)
            with pytest.raises(ValueError):
                await rewind.aclose(drain=drain)
        assert rewind.enabled and not rewind.stats()["closed"]
    finally:
        await rewind.aclose()


async def test_recorder_initialization_failure_releases_reservation(monkeypatch):
    rewind = make_rewind()

    def broken(*args):
        raise RuntimeError("constructor failure")

    async def operation():
        return "application result"

    monkeypatch.setattr("rewind.core.Recorder", broken)
    assert await rewind.run(operation) == "application result"
    assert rewind.stats()["active_captures"] == rewind.stats()["reserved_bytes"] == 0
    assert rewind.stats()["persistence_failed"] == 1


async def test_asgi_cancellation_with_broken_writer_preserves_cancellation(tmp_path, monkeypatch):
    writer = BackgroundWriter(LocalStore(tmp_path / "artifacts"))
    rewind = make_rewind(writer=writer)
    entered = asyncio.Event()

    def reject(snapshot):
        raise OSError("writer failure")

    async def app(scope, receive, send):
        await receive()
        entered.set()
        await asyncio.Future()

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        raise AssertionError("cancelled app must not send a response")

    monkeypatch.setattr(writer, "submit", reject)
    task = asyncio.create_task(rewind.asgi(app)({"type": "http", "headers": []}, receive, send))
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rewind.metrics["persistence_failed"] == 1
        assert rewind.stats()["active_captures"] == 0
        assert current.get() is None
    finally:
        await rewind.aclose()
