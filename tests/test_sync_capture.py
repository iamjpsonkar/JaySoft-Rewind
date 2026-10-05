import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import httpx
import pytest

from rewind import CapturePolicy, LocalStore, Retention, Rewind
from rewind.context import current


@pytest.fixture
def sync_recorder(tmp_path):
    return Rewind(application="sync-tests", code_paths=[__file__],
                  store=LocalStore(tmp_path / "snapshots"), policy=CapturePolicy.synthetic(),
                  retain=Retention(always=True))


def artifact(rewind):
    return rewind.store.load(rewind.store.ids()[-1])


def test_sync_capture_decorator_nesting_and_exact_replay(sync_recorder):
    rewind = sync_recorder

    @rewind.capture()
    def inner(value):
        return value + 1

    @rewind.capture()
    def operation(value, *, increment):
        return {"value": inner(value) + increment, "uuid": rewind.sources.uuid4()}

    result = operation(4, increment=2)
    assert result["value"] == 7
    assert rewind.stats()["admitted"] == 1
    assert current.get() is None
    assert rewind.replay_sync(artifact(rewind), operation).reproduced


def test_sync_exception_and_disabled_capture_preserve_behavior(sync_recorder):
    def operation():
        raise ValueError("original")

    with pytest.raises(ValueError, match="original"):
        sync_recorder.run_sync(operation)
    assert sync_recorder.replay_sync(artifact(sync_recorder), operation).reproduced
    sync_recorder.disable()
    assert sync_recorder.run_sync(lambda: 3) == 3
    assert sync_recorder.stats()["admitted"] == 1
    assert sync_recorder.stats()["active_captures"] == 0


def test_sync_thread_ownership_rejects_copied_context(sync_recorder):
    rewind = sync_recorder

    def operation():
        inherited = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(inherited.run, rewind.sources.uuid4).result()

    rewind.run_sync(operation)
    snapshot = artifact(rewind)
    assert not snapshot.complete
    assert "child_task_unsupported" in snapshot.data["capture"]["ineligible_reasons"]


def test_sync_replay_copied_context_fails_even_when_application_catches(sync_recorder):
    rewind = sync_recorder

    def original():
        return rewind.value("fixed", lambda: 1)

    rewind.run_sync(original)

    def changed():
        inherited = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                pool.submit(inherited.run, rewind.value, "fixed", lambda: 1).result()
            except Exception:
                return 1

    assert sync_recorder.replay_sync(artifact(rewind), changed).status == "diverged"


async def test_sync_dependencies_inside_async_owner_still_supported(sync_recorder):
    rewind = sync_recorder

    async def operation():
        return rewind.run_sync(lambda: rewind.sources.uuid4())

    await rewind.run(operation)
    assert (await rewind.replay(artifact(rewind), operation)).reproduced
    assert rewind.stats()["admitted"] == 1


async def test_sync_and_async_conditions_are_per_decorator(sync_recorder):
    rewind = sync_recorder

    @rewind.capture(when="never")
    def never():
        return 1

    @rewind.capture(when="exception")
    async def conditional(fail):
        await asyncio.sleep(0)
        if fail:
            raise ValueError("wanted")
        return 2

    assert never() == 1
    assert await conditional(False) == 2
    with pytest.raises(ValueError, match="wanted"):
        await conditional(True)
    assert rewind.stats()["retained"] == 1
    assert rewind.retain.always


def test_sync_http_replay_never_calls_live_transport(sync_recorder):
    rewind = sync_recorder
    calls = []

    def live(request):
        calls.append(request)
        return httpx.Response(503, json={"error": "unavailable"})

    with httpx.Client(transport=rewind.httpx_sync_transport(httpx.MockTransport(live))) as client:
        def operation():
            response = client.get("https://service.invalid/status")
            return response.status_code, response.json()

        assert rewind.run_sync(operation) == (503, {"error": "unavailable"})
        assert rewind.replay_sync(artifact(rewind), operation).reproduced
        assert len(calls) == 1


def test_sync_http_recorded_exception_and_sticky_mismatch(sync_recorder):
    rewind = sync_recorder
    calls = []

    def live(request):
        calls.append(request)
        raise httpx.ConnectTimeout("timeout")

    with httpx.Client(transport=rewind.httpx_sync_transport(httpx.MockTransport(live))) as client:
        def operation():
            try:
                client.get("https://service.invalid/status")
            except httpx.ConnectTimeout:
                return "fallback"

        assert rewind.run_sync(operation) == "fallback"
        assert rewind.replay_sync(artifact(rewind), operation).reproduced

        def changed():
            try:
                client.get("https://service.invalid/changed")
            except Exception:
                return "fallback"

        assert rewind.replay_sync(artifact(rewind), changed).status == "diverged"
        assert len(calls) == 1


def test_sync_http_stream_remains_lazy_and_partial_read_is_ineligible(sync_recorder):
    rewind = sync_recorder
    seen = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            seen.append("first")
            yield b"one"
            seen.append("second")
            yield b"two"

        def close(self):
            seen.append("closed")

    with httpx.Client(transport=rewind.httpx_sync_transport(httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Stream())
    ))) as client:
        def operation():
            with client.stream("GET", "https://service.invalid") as response:
                assert not seen
                assert next(response.iter_raw()) == b"one"
                assert seen == ["first"]
            return "partial"

        assert rewind.run_sync(operation) == "partial"
        assert seen == ["first", "closed"]
        assert not artifact(rewind).complete


def test_sync_store_failure_is_fail_open_and_releases_budget(sync_recorder, monkeypatch):
    def fail(snapshot):
        raise OSError("private")

    monkeypatch.setattr(sync_recorder.store, "save", fail)
    assert sync_recorder.run_sync(lambda: "actual") == "actual"
    assert sync_recorder.stats()["active_captures"] == 0
    assert sync_recorder.stats()["persistence_failed"] == 1
