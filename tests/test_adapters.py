import asyncio
import gzip

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from rewind import Retention

from .test_replay import saved


def make_app(recorder, *, stream=False, status=500):
    app = FastAPI()

    @app.post("/payment")
    async def payment(request: Request):
        incoming = await request.json()
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(
                httpx.MockTransport(
                    lambda r: httpx.Response(200, json={"order": incoming["order"]})
                )
            )
        ) as client:
            response = await client.get("https://gateway.example/")
        if stream:

            async def chunks():
                yield b"one"
                yield b"two"

            return StreamingResponse(chunks())
        return JSONResponse(
            {"error": "synthetic", "order": response.json()["order"]}, status_code=status
        )

    return app


async def test_asgi_status_capture_replays_request_without_server(recorder):
    recorder.retain = Retention()
    app = make_app(recorder)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=recorder.asgi(app)), base_url="http://local"
    ) as client:
        response = await client.post("/payment", json={"order": "demo"})
    assert response.status_code == 500
    snapshot = saved(recorder)
    assert snapshot.complete
    report = await recorder.replay_asgi(snapshot, recorder.asgi(app))
    assert report.reproduced, report


async def test_asgi_success_discard_and_body_transparency(recorder):
    recorder.retain = Retention()
    app = make_app(recorder, status=200)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=recorder.asgi(app)), base_url="http://local"
    ) as client:
        response = await client.post("/payment", json={"order": "demo"})
    assert response.json() == {"error": "synthetic", "order": "demo"}
    assert recorder.store.ids() == []


async def test_asgi_streaming_is_transparent_but_not_replayable(recorder):
    app = make_app(recorder, stream=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=recorder.asgi(app)), base_url="http://local"
    ) as client:
        response = await client.post("/payment", json={"order": "demo"})
    assert response.content == b"onetwo"
    assert not saved(recorder).complete


async def test_asgi_unhandled_exception_preserves_sent_response(recorder):
    app = FastAPI()

    @app.post("/broken")
    async def broken(request: Request):
        await request.body()
        raise ValueError("synthetic failure")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=recorder.asgi(app), raise_app_exceptions=False),
        base_url="http://local",
    ) as client:
        response = await client.post("/broken", content=b"")
    assert response.status_code == 500
    assert (await recorder.replay_asgi(saved(recorder), app)).reproduced


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *, fail=False):
        self.closed = False
        self.fail = fail

    async def __aiter__(self):
        yield b"first"
        if self.fail:
            raise httpx.ReadError("synthetic read error")
        yield b"second"

    async def aclose(self):
        self.closed = True


async def test_http_stream_wrapper_preserves_raw_bytes_and_close(recorder):
    stream = Chunks()

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(
                httpx.MockTransport(lambda r: httpx.Response(200, stream=stream))
            )
        ) as client:
            return (await client.get("https://gateway.example/")).content

    assert await recorder.run(operation) == b"firstsecond"
    assert stream.closed
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_early_stream_close_is_ineligible(recorder):
    stream = Chunks()

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(
                httpx.MockTransport(lambda r: httpx.Response(200, stream=stream))
            )
        ) as client:
            async with client.stream("GET", "https://gateway.example/") as response:
                async for _ in response.aiter_bytes():
                    break

    await recorder.run(operation)
    assert stream.closed
    assert not saved(recorder).complete


async def test_midstream_exception_keeps_application_failure(recorder):
    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(
                httpx.MockTransport(lambda r: httpx.Response(200, stream=Chunks(fail=True)))
            )
        ) as client:
            return await client.get("https://gateway.example/")

    with pytest.raises(httpx.ReadError):
        await recorder.run(operation)
    assert not saved(recorder).complete


async def test_compression_is_not_silently_replayed_with_wrong_bytes(recorder):
    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(
                httpx.MockTransport(
                    lambda r: httpx.Response(
                        200,
                        content=gzip.compress(b"original"),
                        headers={"content-encoding": "gzip"},
                    )
                )
            )
        ) as client:
            return (await client.get("https://gateway.example/")).content

    assert await recorder.run(operation) == b"original"
    assert not saved(recorder).complete


async def test_body_limit_never_truncates_application_response(recorder):
    from rewind import Limits

    recorder.limits = Limits(body_bytes=4)

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(
                httpx.MockTransport(lambda r: httpx.Response(200, stream=Chunks()))
            )
        ) as client:
            return (await client.get("https://gateway.example/")).content

    assert await recorder.run(operation) == b"firstsecond"
    assert not saved(recorder).complete


async def test_asgi_cancellation_propagates(recorder):
    async def app(scope, receive, send):
        await receive()
        raise asyncio.CancelledError

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    with pytest.raises(asyncio.CancelledError):
        await recorder.asgi(app)({"type": "http", "method": "POST", "headers": []}, receive, send)
    assert not saved(recorder).complete
