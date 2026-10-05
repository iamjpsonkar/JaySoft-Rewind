"""HTTPX async transport capture at the application-visible I/O boundary."""

import base64
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from .. import context
from ..codecs import decode
from ..errors import RewindError
from ..recorder import Recorder
from ..replay import ReplaySession

_EXCEPTIONS = {
    f"httpx.{name}": getattr(httpx, name)
    for name in (
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "WriteError",
        "CloseError",
        "LocalProtocolError",
        "RemoteProtocolError",
        "ProxyError",
        "UnsupportedProtocol",
    )
}


def request_data(request: httpx.Request, active: Any) -> dict[str, Any]:
    mark = active.mark if isinstance(active, Recorder) else active.fail
    try:
        content = request.content
    except httpx.RequestNotRead:
        mark("streaming_request_unsupported")
        content = b""
    return {
        "method": request.method,
        "url": active.policy.url(str(request.url), mark),
        "headers": active.policy.headers(request.headers.raw, mark),
        "body": active.policy.body(
            content, request.headers.get("content-type", ""), active.limits, mark
        ),
    }


def _replay_response(
    active: ReplaySession, request: httpx.Request, dependency: str
) -> httpx.Response:
    outcome = active.consume("http.request", dependency, request_data(request, active))
    if outcome["kind"] == "exception":
        cls = _EXCEPTIONS.get(outcome["type"])
        try:
            args = decode(outcome["args"], active.limits)
        except RewindError:
            active.fail("invalid recorded HTTP exception")
        if (
            cls is None
            or type(args) is not tuple
            or len(args) != 1
            or type(args[0]) is not str
        ):
            active.fail("unsupported recorded HTTP exception")
        raise cls(args[0], request=request)
    try:
        data = decode(outcome["value"], active.limits)
        if (
            type(data) is not dict
            or type(data["status"]) is not int
            or not 100 <= data["status"] <= 599
            or type(data["body"]) is not str
            or type(data["http_version"]) is not str
            or type(data["headers"]) is not list
            or any(
                type(pair) is not list
                or len(pair) != 2
                or any(type(part) is not str for part in pair)
                for pair in data["headers"]
            )
        ):
            raise ValueError
        body = base64.b64decode(data["body"], validate=True)
        if len(body) > active.limits.body_bytes:
            raise ValueError
        return httpx.Response(
            data["status"],
            headers=data["headers"],
            stream=httpx.ByteStream(body),
            extensions={"http_version": data["http_version"].encode("ascii")},
        )
    except (RewindError, ValueError, KeyError, TypeError, UnicodeError):
        active.fail("invalid recorded HTTP response")


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, transport: httpx.AsyncBaseTransport | None = None, *, dependency: str = "http"
    ) -> None:
        self.inner = transport
        self.dependency = dependency

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        active = context.current.get()
        if isinstance(active, Recorder) and active.sealed:
            active = None
        if isinstance(active, ReplaySession):
            return _replay_response(active, request, self.dependency)
        slot = None
        if isinstance(active, Recorder):
            try:
                slot = active.begin("http.request", self.dependency, request_data(request, active))
            except Exception:
                active.mark("http_input_capture_failed")
        if self.inner is None:
            self.inner = httpx.AsyncHTTPTransport()
        try:
            response = await self.inner.handle_async_request(request)
        except BaseException as exc:
            if isinstance(active, Recorder):
                if f"{type(exc).__module__}.{type(exc).__qualname__}" not in _EXCEPTIONS:
                    active.mark("unsupported_http_exception")
                active.finish(slot, active.raised(exc))
            raise
        if isinstance(active, Recorder) and not active.sealed and slot is not None:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                active.mark("compressed_response_unsupported")
            if response.is_stream_consumed:
                _complete(active, slot, response, response.content)
            else:
                response.stream = RecordingStream(response.stream, active, slot, response)
        return response

    async def aclose(self) -> None:
        if self.inner is not None:
            await self.inner.aclose()


def _complete(recorder: Recorder, slot: int, response: httpx.Response, content: bytes) -> None:
    if recorder.sealed:
        return
    try:
        data = {
            "status": response.status_code,
            "headers": recorder.policy.headers(response.headers.raw, recorder.mark),
            "body": recorder.policy.body(
                content, response.headers.get("content-type", ""), recorder.limits, recorder.mark
            ),
            "http_version": response.http_version,
        }
        recorder.finish(slot, recorder.returned(data))
    except Exception:
        recorder.mark("http_output_capture_failed")


class RecordingStream(httpx.AsyncByteStream):
    def __init__(self, inner: Any, recorder: Recorder, slot: int, response: httpx.Response) -> None:
        self.inner = inner
        self.recorder = recorder
        self.slot = slot
        self.response = response
        self.body = bytearray()
        self.exhausted = False
        self.overflow = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.inner:
                if not self.recorder.sealed and not self.overflow:
                    if len(self.body) + len(chunk) <= self.recorder.limits.body_bytes:
                        self.body.extend(chunk)
                    else:
                        self.body.clear()
                        self.overflow = True
                        self.recorder.mark("body_limit")
                yield chunk
            self.exhausted = True
            _complete(self.recorder, self.slot, self.response, bytes(self.body))
        except BaseException:
            # Partial stream failures cannot be replayed as a pre-response exception.
            self.recorder.mark("response_stream_failed")
            raise
        finally:
            self.body.clear()

    async def aclose(self) -> None:
        if not self.exhausted:
            self.recorder.mark("response_not_consumed")
        self.body.clear()
        await self.inner.aclose()


class RecordingSyncTransport(httpx.BaseTransport):
    """Synchronous HTTPX transport with the same recorded protocol as async HTTP."""

    def __init__(
        self, transport: httpx.BaseTransport | None = None, *, dependency: str = "http"
    ) -> None:
        self.inner = transport
        self.dependency = dependency

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        active = context.current.get()
        if isinstance(active, Recorder) and active.sealed:
            active = None
        if isinstance(active, ReplaySession):
            return _replay_response(active, request, self.dependency)
        slot = None
        if isinstance(active, Recorder):
            try:
                slot = active.begin("http.request", self.dependency, request_data(request, active))
            except Exception:
                active.mark("http_input_capture_failed")
        if self.inner is None:
            self.inner = httpx.HTTPTransport()
        try:
            response = self.inner.handle_request(request)
        except BaseException as exc:
            if isinstance(active, Recorder):
                if f"{type(exc).__module__}.{type(exc).__qualname__}" not in _EXCEPTIONS:
                    active.mark("unsupported_http_exception")
                active.finish(slot, active.raised(exc))
            raise
        if isinstance(active, Recorder) and not active.sealed and slot is not None:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                active.mark("compressed_response_unsupported")
            if response.is_stream_consumed:
                _complete(active, slot, response, response.content)
            else:
                response.stream = RecordingSyncStream(response.stream, active, slot, response)
        return response

    def close(self) -> None:
        if self.inner is not None:
            self.inner.close()


class RecordingSyncStream(httpx.SyncByteStream):
    def __init__(self, inner: Any, recorder: Recorder, slot: int, response: httpx.Response) -> None:
        self.inner = inner
        self.recorder = recorder
        self.slot = slot
        self.response = response
        self.body = bytearray()
        self.exhausted = False
        self.overflow = False

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self.inner:
                if not self.recorder.sealed and not self.overflow:
                    if len(self.body) + len(chunk) <= self.recorder.limits.body_bytes:
                        self.body.extend(chunk)
                    else:
                        self.body.clear()
                        self.overflow = True
                        self.recorder.mark("body_limit")
                yield chunk
            self.exhausted = True
            _complete(self.recorder, self.slot, self.response, bytes(self.body))
        except BaseException:
            self.recorder.mark("response_stream_failed")
            raise
        finally:
            self.body.clear()

    def close(self) -> None:
        if not self.exhausted:
            self.recorder.mark("response_not_consumed")
        self.body.clear()
        self.inner.close()
