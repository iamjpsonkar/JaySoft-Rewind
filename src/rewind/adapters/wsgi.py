"""Lazy WSGI middleware and strict replay of bounded synchronous exchanges."""

import base64
import io
from collections.abc import Callable, Iterator
from typing import Any

from .. import context
from ..codecs import decode, encode
from ..errors import ReplayDivergence, RewindError
from ..recorder import Recorder
from ..replay import ReplayReport, ReplaySession
from ..snapshot import Snapshot

_ENV_KEYS = {
    "REQUEST_METHOD", "SCRIPT_NAME", "PATH_INFO", "QUERY_STRING", "SERVER_NAME",
    "SERVER_PORT", "SERVER_PROTOCOL", "CONTENT_TYPE", "CONTENT_LENGTH", "REMOTE_ADDR",
    "REMOTE_PORT", "wsgi.version", "wsgi.url_scheme", "wsgi.multithread",
    "wsgi.multiprocess", "wsgi.run_once", "wsgi.input_terminated",
    "RAW_URI", "REQUEST_URI",
}
_IO_ERRORS = {
    "builtins.OSError": OSError, "builtins.ValueError": ValueError,
    "builtins.StopIteration": StopIteration,
}


def _mark(active: Any, reason: str) -> None:
    if isinstance(active, Recorder):
        active.mark(reason)
    else:
        active.fail(reason)


class InputStream:
    """Observe only the reads the application actually performs."""

    def __init__(self, inner: Any, active: Any, content_type: str) -> None:
        self.inner = inner
        self.active = active
        self.content_type = content_type
        self.bytes_seen = 0

    def _call(
        self, method: str, args: tuple, invoke: Callable[[], Any], kwargs: dict | None = None
    ) -> Any:
        active = self.active
        descriptor = {"method": method, "args": args, "kwargs": kwargs or {}}
        if isinstance(active, ReplaySession):
            outcome = active.consume("wsgi.read", "wsgi.input", descriptor)
            if outcome["kind"] == "exception":
                cls = _IO_ERRORS.get(outcome["type"])
                values = active.unpack(outcome["args"])
                if cls is None or type(values) is not tuple:
                    active.fail("unsupported WSGI input exception")
                raise cls(*values)
            value = active.unpack(outcome["value"])
            if method != "readinto":
                chunks = value if method == "readlines" and type(value) is list else [value]
                if (
                    any(type(chunk) is not bytes for chunk in chunks)
                    or sum(len(chunk) for chunk in chunks) > active.limits.body_bytes
                    or (method == "readlines" and type(value) is not list)
                ):
                    active.fail("invalid WSGI input outcome")
            return value
        if active.sealed:
            return invoke()
        slot = None
        try:
            slot = active.begin("wsgi.read", "wsgi.input", descriptor)
        except Exception:
            active.mark("wsgi_read_capture_failed")
        try:
            value = invoke()
        except BaseException as exc:
            if f"{type(exc).__module__}.{type(exc).__qualname__}" not in _IO_ERRORS:
                active.mark("wsgi_read_exception_unsupported")
            active.finish(slot, active.raised(exc))
            raise
        try:
            packed = value
            if method == "readinto":
                chunks = [value["data"]]
            else:
                chunks = value if type(value) is list else [value]
            if any(type(chunk) is not bytes for chunk in chunks):
                active.mark("wsgi_read_type_unsupported")
                packed = None
            else:
                self.bytes_seen += sum(len(chunk) for chunk in chunks)
                if self.bytes_seen > active.limits.body_bytes:
                    active.mark("request_body_limit")
                    packed = None
                else:
                    cleaned = [
                        base64.b64decode(active.policy.body(
                            chunk, self.content_type, active.limits, active.mark
                        )) for chunk in chunks
                    ]
                    if method == "readinto":
                        packed = {"count": value["count"], "data": cleaned[0]}
                    else:
                        packed = cleaned if type(value) is list else cleaned[0]
            active.finish(slot, active.returned(packed))
        except Exception:
            active.mark("wsgi_read_capture_failed")
        return value

    def read(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("read", args, lambda: self.inner.read(*args, **kwargs), kwargs)

    def readline(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("readline", args, lambda: self.inner.readline(*args, **kwargs), kwargs)

    def readlines(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("readlines", args, lambda: self.inner.readlines(*args, **kwargs), kwargs)

    def readinto(self, buffer: Any) -> int:
        def invoke() -> dict[str, Any]:
            readinto = getattr(self.inner, "readinto", None)
            if readinto is None:
                data = self.inner.read(len(buffer))
                memoryview(buffer)[:len(data)] = data
                count = len(data)
            else:
                count = readinto(buffer)
            data = b""
            if not (isinstance(self.active, Recorder) and self.active.sealed):
                if (
                    type(count) is not int or not 0 <= count <= len(buffer)
                    or count > self.active.limits.body_bytes - self.bytes_seen
                ):
                    _mark(self.active, "wsgi_readinto_limit_or_type")
                else:
                    data = bytes(memoryview(buffer)[:count])
            return {"count": count, "data": data}

        value = self._call("readinto", (len(buffer),), invoke)
        if isinstance(self.active, ReplaySession):
            if (
                type(value) is not dict or set(value) != {"count", "data"}
                or type(value["count"]) is not int or type(value["data"]) is not bytes
                or not 0 <= value["count"] <= len(buffer)
                or len(value["data"]) != value["count"]
            ):
                self.active.fail("invalid WSGI readinto outcome")
            memoryview(buffer)[:value["count"]] = value["data"]
        return value["count"]

    def __iter__(self) -> Iterator[bytes]:
        if isinstance(self.active, Recorder) and self.active.sealed:
            return iter(self.inner)
        iterator = None
        if isinstance(self.active, Recorder):
            try:
                iterator = iter(self.inner)
            except BaseException:
                self.active.mark("wsgi_input_iterator_unsupported")
                raise
        return _InputIterator(self, iterator)

    def __next__(self) -> bytes:
        return self._call("next", (), lambda: next(self.inner))

    def __getattr__(self, name: str) -> Any:
        if isinstance(self.active, ReplaySession):
            self.active.fail("unsupported WSGI input attribute")
        self.active.mark("wsgi_input_extension_unsupported")
        return getattr(self.inner, name)


class _InputIterator:
    def __init__(self, stream: InputStream, iterator: Iterator | None) -> None:
        self.stream = stream
        self.iterator = iterator

    def __iter__(self) -> "_InputIterator":
        return self

    def __next__(self) -> bytes:
        def invoke() -> bytes:
            assert self.iterator is not None
            return next(self.iterator)

        return self.stream._call("next", (), invoke)


class Exchange:
    def __init__(self, active: Any) -> None:
        self.active = active
        self.status: str | None = None
        self.headers: list = []
        self.chunks: list[tuple[str, bytes]] = []
        self.bytes_seen = 0
        self.error: dict[str, Any] | None = None
        self.exhausted = False
        self.finalized = False

    def exception(self, exc: BaseException, stage: str) -> None:
        if not isinstance(exc, Exception):
            _mark(self.active, "execution_interrupted")
        if isinstance(self.active, Recorder):
            outcome = self.active.raised(exc)
        else:
            outcome = self.active.policy.exception(exc, self.active.limits, self.active.fail)
        self.error = {"stage": stage, "outcome": outcome}

    def append(self, body: bytes, source: str) -> None:
        if self.finalized:
            return
        self.active.check_task()
        if type(body) is not bytes:
            _mark(self.active, "wsgi_response_type_unsupported")
            return
        self.bytes_seen += len(body)
        if (
            self.bytes_seen > self.active.limits.body_bytes
            or len(self.chunks) >= self.active.limits.interactions
        ):
            self.chunks.clear()
            _mark(self.active, "response_body_limit")
            return
        self.chunks.append((source, body))

    def start(self, downstream: Callable) -> Callable:
        def start_response(status: str, headers: list, exc_info: Any = None) -> Callable:
            try:
                write = (
                    downstream(status, headers) if exc_info is None
                    else downstream(status, headers, exc_info)
                )
            except BaseException:
                _mark(self.active, "wsgi_server_exception_unsupported")
                raise
            if not self.finalized:
                try:
                    self.active.check_task()
                    if self.status is not None:
                        _mark(self.active, "wsgi_repeated_start_response")
                    self.status = status
                    self.headers = list(headers)
                except Exception:
                    if isinstance(self.active, ReplaySession):
                        raise
                    self.active.mark("wsgi_header_capture_failed")

            def observed_write(body: bytes) -> Any:
                try:
                    result = write(body)
                except BaseException:
                    _mark(self.active, "wsgi_server_exception_unsupported")
                    raise
                try:
                    self.append(body, "write")
                except Exception:
                    if isinstance(self.active, ReplaySession):
                        raise
                    self.active.mark("wsgi_response_capture_failed")
                return result

            return observed_write

        return start_response

    def outcome(self) -> dict[str, Any]:
        if self.status is None:
            _mark(self.active, "response_incomplete")
        policy = self.active.policy
        def mark(reason: str) -> None:
            _mark(self.active, reason)
        headers = policy.headers(
            [(key.encode("latin1"), value.encode("latin1")) for key, value in self.headers], mark
        )
        content_type = next((value for key, value in headers if key == "content-type"), "")
        chunks = [
            (source, policy.body(body, content_type, self.active.limits, mark))
            for source, body in self.chunks
        ]
        result = {
            "status": self.status, "headers": headers, "chunks": chunks,
            "exhausted": self.exhausted, "error": self.error,
        }
        return (
            self.active.returned(result) if isinstance(self.active, Recorder)
            else {"kind": "return", "value": encode(result, self.active.limits)}
        )

    def clear(self) -> None:
        self.finalized = True
        self.headers.clear()
        self.chunks.clear()
        self.error = None


class ResponseIterator:
    def __init__(self, inner: Any, exchange: Exchange, finish: Callable[[], None]) -> None:
        self.inner = inner
        self.iterator: Iterator | None = None
        self.exchange = exchange
        self.finish = finish
        self.closed = False

    def __iter__(self) -> "ResponseIterator":
        return self

    def __len__(self) -> int:
        # Preserve server optimizations that inspect an iterable's length.
        if type(self.inner) not in (list, tuple) and hasattr(type(self.inner), "__len__"):
            _mark(self.exchange.active, "wsgi_custom_length_unsupported")
        return len(self.inner)

    def __next__(self) -> bytes:
        token = context.current.set(self.exchange.active)
        try:
            if self.iterator is None:
                self.iterator = iter(self.inner)
            chunk = next(self.iterator)
            try:
                self.exchange.append(chunk, "yield")
            except Exception:
                if isinstance(self.exchange.active, ReplaySession):
                    raise
                self.exchange.active.mark("wsgi_response_capture_failed")
            return chunk
        except StopIteration:
            self.exchange.exhausted = True
            raise
        except BaseException as exc:
            self.exchange.exception(exc, "iterate")
            raise
        finally:
            context.current.reset(token)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        token = context.current.set(self.exchange.active)
        try:
            close = getattr(self.inner, "close", None)
            if close is not None:
                close()
        except BaseException as exc:
            self.exchange.exception(exc, "close")
            raise
        finally:
            context.current.reset(token)
            self.finish()


def _environ(environ: dict, active: Recorder) -> dict:
    supported = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers = active.policy.headers(
                [(key[5:].replace("_", "-").encode("latin1"), value.encode("latin1"))], active.mark
            )
            supported[key] = headers[0][1]
        elif key in _ENV_KEYS:
            supported[key] = (
                active.policy.url(value, active.mark) if key in {"RAW_URI", "REQUEST_URI"}
                else value
            )
        elif key not in {"wsgi.input", "wsgi.errors"}:
            active.mark("wsgi_environ_extension_unsupported")
    if supported.get("QUERY_STRING"):
        clean = active.policy.url(
            "http://capture.invalid/?" + supported["QUERY_STRING"], active.mark
        )
        supported["QUERY_STRING"] = clean.split("?", 1)[-1]
    return supported


class CaptureWSGI:
    def __init__(self, app: Any, rewind: Any) -> None:
        self.app = app
        self.rewind = rewind

    def __call__(self, environ: dict, start_response: Callable) -> Any:
        recorder = self.rewind.start("wsgi")
        if recorder is None:
            return self.app(environ, start_response)
        exchange = Exchange(recorder)
        incoming = dict(environ)
        try:
            recorder.set_input({"environ": _environ(environ, recorder)})
            incoming["wsgi.input"] = InputStream(
                environ["wsgi.input"], recorder, environ.get("CONTENT_TYPE", "")
            )
        except Exception:
            recorder.mark("wsgi_environ_capture_failed")

        def finish() -> None:
            status = None
            try:
                if exchange.status is not None:
                    status = int(exchange.status.split()[0])
                if not exchange.exhausted and exchange.error is None:
                    recorder.mark("wsgi_response_not_consumed")
                outcome = exchange.outcome()
            except Exception:
                recorder.mark("wsgi_finalize_failed")
                outcome = recorder.returned(None)
            try:
                self.rewind.finish(
                    recorder, outcome, failed=exchange.error is not None,
                    status=status,
                )
            finally:
                exchange.clear()

        token = context.current.set(recorder)
        try:
            result = self.app(incoming, exchange.start(start_response))
            return ResponseIterator(result, exchange, finish)
        except BaseException as exc:
            exchange.exception(exc, "call")
            finish()
            raise
        finally:
            context.current.reset(token)


def replay_wsgi(rewind: Any, snapshot: Snapshot, app: Any) -> ReplayReport:
    failure = rewind.preflight(snapshot, "wsgi")
    if failure:
        return failure
    session = ReplaySession(snapshot, rewind.limits)
    incoming = decode(session.data["input"]["value"], rewind.limits)["environ"]
    incoming["wsgi.input"] = InputStream(None, session, incoming.get("CONTENT_TYPE", ""))
    incoming["wsgi.errors"] = io.StringIO()
    exchange = Exchange(session)
    response = None
    token = context.current.set(session)
    try:
        try:
            response = app(incoming, exchange.start(lambda *args: lambda body: None))
        except ReplayDivergence:
            raise
        except Exception as exc:
            exchange.exception(exc, "call")
        if response is not None:
            wrapped = ResponseIterator(response, exchange, lambda: None)
            try:
                for _ in wrapped:
                    pass
            except ReplayDivergence:
                raise
            except Exception:
                pass  # ResponseIterator already recorded the application exception.
            finally:
                try:
                    wrapped.close()
                except ReplayDivergence:
                    raise
                except Exception:
                    pass
        return session.report(exchange.outcome())
    except ReplayDivergence:
        session.failure = session.failure or "application raised replay divergence"
        return session.report({"kind": "return", "value": encode(None, rewind.limits)})
    except RewindError:
        return ReplayReport("replay_error", "unsupported WSGI outcome", session.cursor,
                            len(session.interactions))
    finally:
        session.closed = True
        exchange.clear()
        context.current.reset(token)
