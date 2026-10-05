"""Public recording and replay lifecycle."""

import asyncio
import functools
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from . import context
from .codecs import decode, encode
from .conditions import Condition, Retention
from .errors import ReplayDivergence, RewindError
from .fingerprint import fingerprint
from .limits import Limits
from .persistence import BackgroundWriter, ShutdownReport, _timeout
from .policy import CapturePolicy
from .recorder import Recorder
from .replay import ReplayReport, ReplaySession
from .snapshot import Snapshot
from .sources import Sources
from .storage import LocalStore
from .tracing import TraceConfig

T = TypeVar("T")
_COUNTERS = (
    "admitted",
    "admission_rejected",
    "retained",
    "incomplete",
    "persisted",
    "persistence_failed",
    "submitted",
    "enqueue_rejected",
    "closed_rejected",
    "dropped",
)


class Rewind:
    def __init__(
        self,
        *,
        application: str,
        code_paths: list[str | Path],
        store: LocalStore | None = None,
        writer: BackgroundWriter | None = None,
        policy: CapturePolicy | None = None,
        limits: Limits | None = None,
        retain: Retention | Condition | None = None,
        enabled: bool = True,
        trace_config: TraceConfig | None = None,
    ) -> None:
        if store is not None and writer is not None:
            raise ValueError("store and writer are mutually exclusive")
        self.application = fingerprint(application, code_paths)
        self.limits = limits or Limits()
        self.policy = policy or CapturePolicy()
        self.store = store
        self.retain = retain or Retention()
        self.writer = writer
        self.sources = Sources()
        if trace_config is not None and not isinstance(trace_config, TraceConfig):
            raise TypeError("trace_config must be TraceConfig")
        self.trace_config = trace_config or TraceConfig()
        self._metrics: Counter[str] = Counter({key: 0 for key in _COUNTERS})
        self._active = 0
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._closed = False

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled and not self._closed

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if value:
            self.enable()
        else:
            self.disable()

    def enable(self) -> None:
        """Resume admission; shutdown is terminal and cannot be reversed."""
        with self._lock:
            if self._closed:
                raise RuntimeError("capture is closed")
            self._enabled = True

    def disable(self) -> None:
        """Stop new captures while already admitted executions finish normally."""
        with self._lock:
            self._enabled = False

    def stats(self) -> dict[str, int | bool]:
        """Return a safe copy of fixed counters and conservative memory reservations.

        Writer counters describe its entire lifetime. Dedicate one writer to this
        instance when per-application accounting is required.
        """
        with self._lock:
            result: dict[str, int | bool] = dict(self._metrics)
            result.update(
                active_captures=self._active,
                reserved_bytes=self._active * self.limits.snapshot_bytes,
                pending_items=0,
                pending_bytes=0,
                enabled=self._enabled and not self._closed,
                closed=self._closed,
            )
            if self.writer is not None:
                writer = self.writer.stats()
                result["persisted"] += int(writer["saved"])
                result["persistence_failed"] += int(writer["failed"])
                result["dropped"] = int(writer["dropped"])
                result["pending_items"] = int(writer["pending_items"])
                result["pending_bytes"] = int(writer["pending_bytes"])
            return result

    @property
    def metrics(self) -> Counter[str]:
        """Compatibility counter view; mutate neither live counters nor metric labels."""
        current = self.stats()
        return Counter({key: int(current[key]) for key in _COUNTERS})

    def flush(self, timeout: float = 5.0) -> bool:
        """Bound the wait for queued artifacts; active application work is excluded."""
        timeout = _timeout(timeout)
        return self.writer.flush(timeout=timeout) if self.writer is not None else True

    async def aflush(self, timeout: float = 5.0) -> bool:  # noqa: ASYNC109
        """Wait for the writer off the event loop without stopping new admission."""
        # The timeout bounds a thread wait; cancelling the coroutine cannot replace it.
        return await asyncio.to_thread(self.flush, timeout)

    def close(self, timeout: float = 5.0, *, drain: bool = True) -> ShutdownReport:
        """Stop admission and bound writer shutdown; never wait for application tasks.

        Active captures may continue executing, but their final artifacts are rejected
        after this call. Stop accepting requests and await application tasks before
        closing if their artifacts must be included.
        """
        # Validate before making a terminal state change, including without a writer.
        timeout = _timeout(timeout)
        if type(drain) is not bool:
            raise ValueError("drain must be a boolean")
        with self._lock:
            self._closed = True
            self._enabled = False
        if self.writer is not None:
            return self.writer.close(timeout=timeout, drain=drain)
        return ShutdownReport(
            drained=True, pending=0, in_flight=False, dropped=0, worker_alive=False
        )

    async def aclose(
        self,
        timeout: float = 5.0,  # noqa: ASYNC109
        *,
        drain: bool = True,
    ) -> ShutdownReport:
        """Run bounded shutdown off the event loop; cancellation does not stop the worker."""
        # Admission stops synchronously before yielding to the executor.
        timeout = _timeout(timeout)
        if type(drain) is not bool:
            raise ValueError("drain must be a boolean")
        with self._lock:
            self._closed = True
            self._enabled = False
        # Shield executor dispatch too: cancellation while the pool is occupied
        # must not leave a terminal capture instance with an open writer.
        return await asyncio.shield(asyncio.to_thread(self.close, timeout, drain=drain))

    def start(self, kind: str) -> Recorder | None:
        if context.current.get() is not None:
            return None
        with self._lock:
            if not self._enabled or self._closed:
                return None
            if (self._active + 1) * self.limits.snapshot_bytes > self.limits.active_bytes:
                self._metrics["admission_rejected"] += 1
                return None
            self._active += 1
            self._metrics["admitted"] += 1
        try:
            return Recorder(
                self.application, self.policy, self.limits, kind, trace_config=self.trace_config
            )
        except Exception:
            with self._lock:
                self._active -= 1
                self._metrics["persistence_failed"] += 1
            return None

    def finish(
        self,
        recorder: Recorder,
        outcome: dict[str, Any],
        *,
        failed: bool,
        status: int | None = None,
        retain: Retention | Condition | None = None,
    ) -> None:
        try:
            with self._lock:
                if self._closed:
                    self._metrics["closed_rejected"] += 1
                    return
            if not (retain or self.retain).matches(
                failed=failed, status=status, duration=time.monotonic() - recorder.started
            ):
                return
            snapshot = recorder.seal(outcome)
            with self._lock:
                self._metrics["retained"] += 1
                if not snapshot.complete:
                    self._metrics["incomplete"] += 1
                # Linearize admission to the writer with terminal shutdown. submit
                # never performs store I/O and retains only immutable snapshot bytes.
                if self._closed:
                    self._metrics["closed_rejected"] += 1
                    return
                if self.writer is not None:
                    key = "submitted" if self.writer.submit(snapshot) else "enqueue_rejected"
                    self._metrics[key] += 1
                    return
            if self.store is not None:
                self.store.save(snapshot)
                with self._lock:
                    self._metrics["persisted"] += 1
        except Exception:
            # Diagnostics contain no application payload or arbitrary exception message.
            with self._lock:
                self._metrics["persistence_failed"] += 1
        finally:
            recorder.sealed = True
            # Inherited task contexts can retain a recorder after the request ends.
            # Release captured payloads even when persistence was rejected or failed.
            recorder.interactions.clear()
            recorder.input = encode(None, self.limits)
            recorder.bytes_used = 0
            recorder.interaction_started.clear()
            if recorder.diagnostics is not None:
                try:
                    recorder.diagnostics.clear()
                except Exception:
                    pass
            with self._lock:
                self._active -= 1

    async def run(self, function: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        return await self._run_async(function, args, kwargs, None)

    async def _run_async(
        self,
        function: Callable[..., Awaitable[T]],
        args: tuple,
        kwargs: dict,
        retain: Condition | None,
    ) -> T:
        recorder = self.start("callable")
        if recorder is None:
            return await function(*args, **kwargs)
        recorder.set_input({"args": args, "kwargs": kwargs})
        token = context.current.set(recorder)
        outcome = {"kind": "return", "value": encode(None, self.limits)}
        failed = False
        try:
            result = await function(*args, **kwargs)
            outcome = recorder.returned(result)
            return result
        except BaseException as exc:
            failed = True
            if not isinstance(exc, Exception):
                recorder.mark("execution_interrupted")
            outcome = recorder.raised(exc)
            raise
        finally:
            context.current.reset(token)
            self.finish(recorder, outcome, failed=failed, retain=retain)

    def run_sync(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Capture a synchronous entry point on its calling thread."""
        return self._run_sync(function, args, kwargs, None)

    def _run_sync(
        self,
        function: Callable[..., T],
        args: tuple,
        kwargs: dict,
        retain: Condition | None,
    ) -> T:
        recorder = self.start("callable_sync")
        if recorder is None:
            return function(*args, **kwargs)
        recorder.set_input({"args": args, "kwargs": kwargs})
        token = context.current.set(recorder)
        outcome = {"kind": "return", "value": encode(None, self.limits)}
        failed = False
        try:
            result = function(*args, **kwargs)
            outcome = recorder.returned(result)
            return result
        except BaseException as exc:
            failed = True
            if not isinstance(exc, Exception):
                recorder.mark("execution_interrupted")
            outcome = recorder.raised(exc)
            raise
        finally:
            context.current.reset(token)
            self.finish(recorder, outcome, failed=failed, retain=retain)

    def capture(self, *, when: str | None = None) -> Callable:
        retain = Condition.parse(when) if when is not None else None

        def decorate(function: Callable) -> Callable:
            if not asyncio.iscoroutinefunction(function):

                @functools.wraps(function)
                def synchronous(*args: Any, **kwargs: Any) -> Any:
                    return self._run_sync(function, args, kwargs, retain)

                return synchronous

            @functools.wraps(function)
            async def wrapped(*args: Any, **kwargs: Any) -> T:
                return await self._run_async(function, args, kwargs, retain)

            return wrapped

        return decorate

    def trace(self, function: Any = None, *, name: str | None = None) -> Any:
        """Add optional diagnostic spans; enable with TraceConfig(enabled=True)."""
        from .tracing import trace

        return trace(function, name=name)

    def replay_sync(self, snapshot: Snapshot, function: Callable[..., Any]) -> ReplayReport:
        """Replay a synchronous callable without creating an event loop."""
        failure = self.preflight(snapshot, "callable_sync")
        if failure:
            return failure
        session = ReplaySession(snapshot, self.limits)
        incoming = decode(session.data["input"]["value"], self.limits)
        token = context.current.set(session)
        try:
            try:
                result = function(*incoming["args"], **incoming["kwargs"])
                outcome = {"kind": "return", "value": encode(result, self.limits)}
            except ReplayDivergence:
                session.failure = session.failure or "application raised replay divergence"
                outcome = {"kind": "return", "value": encode(None, self.limits)}
            except Exception as exc:
                outcome = session.policy.exception(exc, self.limits, session.fail)
            return session.report(outcome)
        except ReplayDivergence:
            session.failure = session.failure or "application raised replay divergence"
            return session.report({"kind": "return", "value": encode(None, self.limits)})
        except RewindError:
            return ReplayReport(
                "replay_error",
                "unsupported replay value or adapter outcome",
                session.cursor,
                len(session.interactions),
            )
        finally:
            session.closed = True
            context.current.reset(token)

    def preflight(self, snapshot: Snapshot, kind: str) -> ReplayReport | None:
        data = Snapshot.from_bytes(snapshot.raw, self.limits).data
        if not data["capture"]["complete"]:
            return ReplayReport("ineligible", ", ".join(data["capture"]["ineligible_reasons"]))
        if data["application"] != self.application:
            return ReplayReport("incompatible", "application source or runtime fingerprint differs")
        if data["input"]["kind"] != kind:
            return ReplayReport("incompatible", "entry point kind differs")
        if context.current.get() is not None:
            return ReplayReport("replay_error", "cannot nest replay inside another execution")
        return None

    async def replay(
        self, snapshot: Snapshot, function: Callable[..., Awaitable[Any]]
    ) -> ReplayReport:
        failure = self.preflight(snapshot, "callable")
        if failure:
            return failure
        session = ReplaySession(snapshot, self.limits)
        incoming = decode(session.data["input"]["value"], self.limits)
        token = context.current.set(session)
        try:
            try:
                result = await function(*incoming["args"], **incoming["kwargs"])
                outcome = {"kind": "return", "value": encode(result, self.limits)}
            except ReplayDivergence:
                session.failure = session.failure or "application raised replay divergence"
                outcome = {"kind": "return", "value": encode(None, self.limits)}
            except Exception as exc:
                outcome = session.policy.exception(exc, self.limits, session.fail)
            return session.report(outcome)
        except ReplayDivergence:
            session.failure = session.failure or "application raised replay divergence"
            return session.report({"kind": "return", "value": encode(None, self.limits)})
        except RewindError:
            return ReplayReport(
                "replay_error",
                "unsupported replay value or adapter outcome",
                session.cursor,
                len(session.interactions),
            )
        finally:
            session.closed = True
            context.current.reset(token)

    def value(self, name: str, factory: Callable[[], T]) -> T:
        """Capture explicit clock/ID/random observations without global patching."""
        active = context.current.get()
        if isinstance(active, ReplaySession):
            outcome = active.consume("value", name, None)
            if outcome["kind"] != "return":
                active.fail("recorded provider exception is unsupported")
            return active.unpack(outcome["value"])
        if isinstance(active, Recorder) and active.sealed:
            return factory()
        slot = active.begin("value", name, None) if isinstance(active, Recorder) else None
        try:
            result = factory()
        except BaseException:
            if isinstance(active, Recorder):
                active.mark("provider_exception_unsupported")
            raise
        if isinstance(active, Recorder) and slot is not None:
            active.finish(slot, active.returned(result))
        return result

    def httpx_transport(self, transport: Any = None, *, dependency: str = "http") -> Any:
        from .adapters.httpx import RecordingTransport

        return RecordingTransport(transport, dependency=dependency)

    def httpx_sync_transport(self, transport: Any = None, *, dependency: str = "http") -> Any:
        from .adapters.httpx import RecordingSyncTransport

        return RecordingSyncTransport(transport, dependency=dependency)

    def wsgi(self, app: Any) -> Any:
        from .adapters.wsgi import CaptureWSGI

        return CaptureWSGI(app, self)

    def replay_wsgi(self, snapshot: Snapshot, app: Any) -> ReplayReport:
        from .adapters.wsgi import replay_wsgi

        return replay_wsgi(self, snapshot, app)

    def asgi(self, app: Any) -> Any:
        from .adapters.asgi import CaptureMiddleware

        return CaptureMiddleware(app, self)

    async def replay_asgi(self, snapshot: Snapshot, app: Any) -> ReplayReport:
        from .adapters.asgi import replay_asgi

        return await replay_asgi(self, snapshot, app)
