"""Pure ASGI capture without consuming request bodies ahead of the application."""

import base64
from typing import Any

from .. import context
from ..codecs import decode, encode
from ..errors import ReplayDivergence, RewindError
from ..recorder import Recorder
from ..replay import ReplayReport, ReplaySession
from ..snapshot import Snapshot

_SCOPE_KEYS = (
    "type", "asgi", "http_version", "method", "scheme", "path", "raw_path",
    "root_path", "query_string", "server", "client", "headers",
)


class Exchange:
    def __init__(self, active: Any, scope: dict[str, Any]) -> None:
        self.active = active
        try:
            self.scope = decode(
                encode({key: scope[key] for key in _SCOPE_KEYS if key in scope}, active.limits),
                active.limits,
            )
        except Exception:
            self.scope = {"type": "http", "headers": []}
            self.mark("scope_capture_failed")
        if any(value for key, value in scope.items() if key not in _SCOPE_KEYS):
            self.mark("asgi_scope_unsupported")
        self.request = bytearray()
        self.response = bytearray()
        self.request_complete = False
        self.response_complete = False
        self.status: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.request_overflow = False
        self.response_overflow = False
        self.request_lengths: list[int] = []

    def mark(self, reason: str) -> None:
        if isinstance(self.active, Recorder):
            self.active.mark(reason)
        else:
            self.active.fail(reason)

    def append(self, body: bytearray, chunk: bytes, side: str) -> None:
        if getattr(self, f"{side}_overflow"):
            return
        if len(body) + len(chunk) > self.active.limits.body_bytes:
            body.clear()
            setattr(self, f"{side}_overflow", True)
            self.mark(f"{side}_body_limit")
        else:
            body.extend(chunk)

    def check_task(self) -> None:
        self.active.check_task()

    def received(self, message: dict[str, Any]) -> None:
        if isinstance(self.active, Recorder) and self.active.sealed:
            return
        self.check_task()
        if message["type"] == "http.disconnect":
            self.mark("client_disconnected")
        elif message["type"] == "http.request":
            chunk = message.get("body", b"")
            self.append(self.request, chunk, "request")
            if len(self.request_lengths) >= self.active.limits.interactions:
                self.mark("request_chunk_limit")
            else:
                self.request_lengths.append(len(chunk))
            if not message.get("more_body", False):
                self.request_complete = True

    def sent(self, message: dict[str, Any]) -> None:
        if isinstance(self.active, Recorder) and self.active.sealed:
            return
        self.check_task()
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = list(message.get("headers", []))
            if message.get("trailers"):
                self.mark("response_trailers_unsupported")
        elif message["type"] == "http.response.body":
            self.append(self.response, message.get("body", b""), "response")
            if message.get("more_body", False):
                self.mark("streaming_response_unsupported")
            else:
                self.response_complete = True
        else:
            self.mark("asgi_extension_unsupported")

    def incoming(self) -> dict[str, Any]:
        policy = self.active.policy
        # Preserve only the explicitly supported HTTP scope; never serialize arbitrary state.
        scope = {key: value for key, value in self.scope.items() if key != "headers"}
        headers = policy.headers(self.scope.get("headers", []), self.mark)
        scope["headers"] = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers]
        query = self.scope.get("query_string", b"")
        if query:
            clean_url = policy.url("http://capture.invalid/?" + query.decode("ascii"), self.mark)
            scope["query_string"] = clean_url.split("?", 1)[-1].encode("ascii")
        content_type = next((v for k, v in headers if k == "content-type"), "")
        body = policy.body(bytes(self.request), content_type, self.active.limits, self.mark)
        if not self.request_complete:
            # Do not drain an unread request just for capture: that changes application behavior.
            self.mark("request_body_not_consumed")
        return {"scope": scope, "body": base64.b64decode(body), "chunks": self.request_lengths}

    def outgoing(self) -> dict[str, Any]:
        if self.status is None or not self.response_complete:
            self.mark("response_incomplete")
        headers = self.active.policy.headers(self.headers, self.mark)
        content_type = next((v for k, v in headers if k == "content-type"), "")
        body = self.active.policy.body(
            bytes(self.response), content_type, self.active.limits, self.mark
        )
        return {"status": self.status, "headers": headers, "body": body}


class CaptureMiddleware:
    def __init__(self, app: Any, rewind: Any) -> None:
        self.app = app
        self.rewind = rewind

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        recorder = self.rewind.start("asgi")
        if recorder is None:
            await self.app(scope, receive, send)
            return
        exchange = Exchange(recorder, scope)
        token = context.current.set(recorder)
        exception: BaseException | None = None

        async def receive_capture() -> dict[str, Any]:
            message = await receive()
            if recorder.sealed:
                return message
            try:
                exchange.received(message)
            except Exception:
                recorder.mark("request_capture_failed")
            return message

        async def send_capture(message: dict[str, Any]) -> None:
            if recorder.sealed:
                await send(message)
                return
            try:
                observed = dict(message)
                if "headers" in observed:
                    observed["headers"] = list(observed["headers"])
            except Exception:
                recorder.mark("response_capture_failed")
                observed = None
            await send(message)
            try:
                if observed is not None:
                    exchange.sent(observed)
            except Exception:
                recorder.mark("response_capture_failed")

        try:
            await self.app(scope, receive_capture, send_capture)
        except BaseException as exc:
            exception = exc
            if not isinstance(exc, Exception):
                recorder.mark("execution_interrupted")
            raise
        finally:
            context.current.reset(token)
            try:
                recorder.set_input(exchange.incoming())
                outcome = (
                    recorder.returned(exchange.outgoing())
                    if exchange.response_complete
                    else recorder.raised(exception or RuntimeError("incomplete response"))
                )
                if exception is not None and exchange.response_complete:
                    # ServerErrorMiddleware may send a 500 then re-raise; preserve both.
                    value = exchange.outgoing()
                    value["exception"] = recorder.raised(exception)
                    outcome = recorder.returned(value)
            except Exception:
                recorder.mark("asgi_finalize_failed")
                outcome = recorder.returned(None)
            exchange.request.clear()
            exchange.response.clear()
            exchange.scope.clear()
            exchange.headers.clear()
            exchange.request_lengths.clear()
            self.rewind.finish(
                recorder, outcome, failed=exception is not None, status=exchange.status
            )


async def replay_asgi(rewind: Any, snapshot: Snapshot, app: Any) -> ReplayReport:
    failure = rewind.preflight(snapshot, "asgi")
    if failure:
        return failure
    session = ReplaySession(snapshot, rewind.limits)
    incoming = decode(session.data["input"]["value"], rewind.limits)
    exchange = Exchange(session, incoming["scope"])
    delivered = 0
    offset = 0
    token = context.current.set(session)
    exception: Exception | None = None

    async def receive() -> dict[str, Any]:
        nonlocal delivered, offset
        if delivered >= len(incoming["chunks"]):
            session.fail("unexpected ASGI receive after recorded request")
        length = incoming["chunks"][delivered]
        delivered += 1
        message = {
            "type": "http.request",
            "body": incoming["body"][offset : offset + length],
            "more_body": delivered < len(incoming["chunks"]),
        }
        offset += length
        exchange.received(message)
        return message

    async def send(message: dict[str, Any]) -> None:
        exchange.sent(message)

    try:
        try:
            await app(incoming["scope"], receive, send)
        except ReplayDivergence:
            pass
        except Exception as exc:
            exception = exc
        if not session.failure and delivered != len(incoming["chunks"]):
            session.fail("recorded ASGI request chunks left unread")
        if session.failure:
            return session.report({"kind": "return", "value": encode(None, rewind.limits)})
        if exchange.response_complete:
            value = exchange.outgoing()
            if exception is not None:
                value["exception"] = session.policy.exception(
                    exception, rewind.limits, session.fail
                )
            outcome = {"kind": "return", "value": encode(value, rewind.limits)}
        else:
            outcome = session.policy.exception(
                exception or RuntimeError("incomplete response"), rewind.limits, session.fail
            )
        return session.report(outcome)
    except ReplayDivergence:
        return session.report({"kind": "return", "value": encode(None, rewind.limits)})
    except RewindError:
        return ReplayReport(
            "replay_error", "unsupported ASGI replay outcome", session.cursor,
            len(session.interactions),
        )
    finally:
        session.closed = True
        context.current.reset(token)
