import asyncio

import pytest

from rewind.codecs import decode
from rewind.context import current

from .test_replay import saved


def scope():
    return {
        "type": "http",
        "method": "POST",
        "path": "/original",
        "headers": [(b"content-type", b"text/plain")],
        "asgi": {"version": "3.0"},
    }


async def capture(recorder, app, *, incoming=None):
    messages = iter(incoming or [{"type": "http.request", "body": b"hello"}])
    outgoing = []

    async def receive():
        return next(messages)

    async def send(message):
        outgoing.append(message)

    await recorder.asgi(app)(scope(), receive, send)
    return outgoing


async def respond(send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def test_replay_detects_unread_request_despite_matching_response(recorder):
    should_read = True

    async def app(scope, receive, send):
        if should_read:
            await receive()
        await respond(send)

    await capture(recorder, app)
    should_read = False
    report = await recorder.replay_asgi(saved(recorder), app)
    assert report.status == "diverged"
    assert "unread" in report.detail


async def test_replay_extra_receive_diverges_without_hanging(recorder):
    extra_read = False

    async def app(scope, receive, send):
        await receive()
        if extra_read:
            try:
                await receive()
            except Exception:
                pass
        await respond(send)

    await capture(recorder, app)
    extra_read = True
    report = await asyncio.wait_for(recorder.replay_asgi(saved(recorder), app), timeout=1)
    assert report.status == "diverged"
    assert "unexpected ASGI receive" in report.detail


async def test_request_chunk_boundaries_are_preserved(recorder):
    seen = []

    async def app(scope, receive, send):
        chunks = []
        while True:
            message = await receive()
            chunks.append(message["body"])
            if not message.get("more_body", False):
                break
        seen.append(chunks)
        await respond(send)

    await capture(recorder, app, incoming=[
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"", "more_body": True},
        {"type": "http.request", "body": b"c"},
    ])
    assert (await recorder.replay_asgi(saved(recorder), app)).reproduced
    assert seen == [[b"ab", b"", b"c"], [b"ab", b"", b"c"]]


async def test_scope_is_detached_before_application_mutates_it(recorder):
    paths = []

    async def app(scope, receive, send):
        paths.append(scope["path"])
        scope["path"] = "/mutated"
        scope["asgi"]["version"] = "changed"
        scope["headers"].clear()
        await receive()
        await respond(send)

    await capture(recorder, app)
    snapshot = saved(recorder)
    incoming = decode(snapshot.data["input"]["value"], recorder.limits)
    assert incoming["scope"]["path"] == "/original"
    assert incoming["scope"]["asgi"] == {"version": "3.0"}
    assert incoming["scope"]["headers"] == [(b"content-type", b"text/plain")]
    assert (await recorder.replay_asgi(snapshot, app)).reproduced
    assert paths == ["/original", "/original"]


async def test_response_headers_are_detached_at_send(recorder):
    async def app(scope, receive, send):
        await receive()
        headers = [(b"x-value", b"before")]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        headers.clear()
        await send({"type": "http.response.body", "body": b"ok"})

    await capture(recorder, app)
    snapshot = saved(recorder)
    outcome = decode(snapshot.data["outcome"]["value"], recorder.limits)
    assert outcome["headers"] == [["x-value", "before"]]
    assert (await recorder.replay_asgi(snapshot, app)).reproduced


async def test_child_task_asgi_io_is_ineligible(recorder):
    async def app(scope, receive, send):
        await asyncio.create_task(receive())
        await respond(send)

    await capture(recorder, app)
    assert "child_task_unsupported" in saved(recorder).data["capture"]["ineligible_reasons"]


async def test_child_task_asgi_io_divergence_is_sticky(recorder):
    use_child = False

    async def app(scope, receive, send):
        if use_child:
            try:
                await asyncio.create_task(receive())
            except Exception:
                pass
        else:
            await receive()
        await respond(send)

    await capture(recorder, app)
    use_child = True
    report = await recorder.replay_asgi(saved(recorder), app)
    assert report.status == "diverged"
    assert "child task" in report.detail


async def test_replay_cancellation_propagates_and_resets_context(recorder):
    cancelled = False

    async def app(scope, receive, send):
        await receive()
        if cancelled:
            raise asyncio.CancelledError
        await respond(send)

    await capture(recorder, app)
    cancelled = True
    with pytest.raises(asyncio.CancelledError):
        await recorder.replay_asgi(saved(recorder), app)
    assert current.get() is None
