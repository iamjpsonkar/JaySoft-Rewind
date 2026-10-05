import asyncio

import httpx
import pytest

from rewind import CapturePolicy, Snapshot
from rewind.codecs import encode
from rewind.context import current

from .test_replay import saved


async def test_sealed_recorder_provider_does_not_encode_late_result(recorder, monkeypatch):
    captured = None

    async def operation():
        nonlocal captured
        captured = current.get()

    await recorder.run(operation)
    calls = []

    def unexpected_pack(value):
        calls.append(value)
        raise AssertionError("sealed recorder must not encode values")

    monkeypatch.setattr(captured, "returned", unexpected_pack)
    token = current.set(captured)
    try:
        assert recorder.value("late", lambda: "original") == "original"
    finally:
        current.reset(token)
    assert calls == []


async def test_sealed_recorder_transport_passes_through_without_inspection(recorder, monkeypatch):
    captured = None

    async def operation():
        nonlocal captured
        captured = current.get()

    await recorder.run(operation)
    calls = []

    def unexpected_begin(*args):
        calls.append(args)
        raise AssertionError("sealed recorder must not inspect dependencies")

    monkeypatch.setattr(captured, "begin", unexpected_begin)
    token = current.set(captured)
    try:
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(lambda r: httpx.Response(201)))
        ) as client:
            response = await client.get("https://example.invalid/")
    finally:
        current.reset(token)
    assert response.status_code == 201
    assert calls == []


@pytest.mark.parametrize("bad_headers", [1, [[1, "value"]], [["name"]]])
async def test_malformed_response_stays_diverged_when_application_catches_it(recorder, bad_headers):
    live_calls = 0

    async def transport(request):
        nonlocal live_calls
        live_calls += 1
        return httpx.Response(200)

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(transport))
        ) as client:
            try:
                await client.get("https://example.invalid/")
            except Exception:
                pass

    await recorder.run(operation)
    data = saved(recorder).data
    data["interactions"][0]["outcome"] = {
        "kind": "return",
        "value": encode({"status": 200, "body": "", "headers": bad_headers,
                         "http_version": "HTTP/1.1"}, recorder.limits),
    }
    report = await recorder.replay(Snapshot.from_dict(data), operation)
    assert report.status == "diverged"
    assert report.detail == "invalid recorded HTTP response"
    assert live_calls == 1


async def test_replay_thread_provider_mismatch_is_sticky(recorder):
    threaded = False

    async def operation():
        if threaded:
            try:
                await asyncio.to_thread(recorder.value, "id", lambda: 2)
            except Exception:
                pass
        return recorder.value("id", lambda: 1)

    await recorder.run(operation)
    threaded = True
    report = await recorder.replay(saved(recorder), operation)
    assert report.status == "diverged"
    assert "child task" in report.detail


async def test_policy_rejection_of_replay_exception_is_divergence(recorder):
    recorder.policy = CapturePolicy()
    fails = False

    async def operation():
        if fails:
            raise ValueError("private-value")

    # Callable arguments are excluded by this policy, so produce a complete
    # synthetic snapshot with identical captured input and disabled exception args.
    recorder.policy = CapturePolicy.synthetic()
    await recorder.run(operation)
    data = saved(recorder).data
    data["policy"]["exception_args"] = False
    fails = True
    report = await recorder.replay(Snapshot.from_dict(data), operation)
    assert report.status == "diverged"
    assert "private-value" not in report.detail


async def test_http_cancellation_preserves_original_and_never_eligible(recorder):
    async def transport(request):
        raise asyncio.CancelledError

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(transport))
        ) as client:
            await client.get("https://example.invalid/")

    with pytest.raises(asyncio.CancelledError):
        await recorder.run(operation)
    assert not saved(recorder).complete
    assert current.get() is None
