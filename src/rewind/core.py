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
from .conditions import Retention
from .errors import ReplayDivergence, RewindError
from .fingerprint import fingerprint
from .limits import Limits
from .policy import CapturePolicy
from .recorder import Recorder
from .replay import ReplayReport, ReplaySession
from .snapshot import Snapshot
from .storage import LocalStore

T = TypeVar("T")


class Rewind:
    def __init__(
        self,
        *,
        application: str,
        code_paths: list[str | Path],
        store: LocalStore | None = None,
        policy: CapturePolicy | None = None,
        limits: Limits | None = None,
        retain: Retention | None = None,
        enabled: bool = True,
    ) -> None:
        self.application = fingerprint(application, code_paths)
        self.limits = limits or Limits()
        self.policy = policy or CapturePolicy()
        self.store = store
        self.retain = retain or Retention()
        self.enabled = enabled
        self.metrics: Counter[str] = Counter()
        self._active = 0
        self._lock = threading.Lock()

    def start(self, kind: str) -> Recorder | None:
        if not self.enabled or context.current.get() is not None:
            return None
        with self._lock:
            if (self._active + 1) * self.limits.snapshot_bytes > self.limits.active_bytes:
                self.metrics["admission_rejected"] += 1
                return None
            self._active += 1
        self.metrics["admitted"] += 1
        return Recorder(self.application, self.policy, self.limits, kind)

    def finish(
        self,
        recorder: Recorder,
        outcome: dict[str, Any],
        *,
        failed: bool,
        status: int | None = None,
    ) -> None:
        try:
            if not self.retain.matches(
                failed=failed, status=status, duration=time.monotonic() - recorder.started
            ):
                return
            snapshot = recorder.seal(outcome)
            self.metrics["retained"] += 1
            if not snapshot.complete:
                self.metrics["incomplete"] += 1
            if self.store is not None:
                self.store.save(snapshot)
                self.metrics["persisted"] += 1
        except Exception:
            # Diagnostics contain no application payload or arbitrary exception message.
            self.metrics["persistence_failed"] += 1
        finally:
            recorder.sealed = True
            with self._lock:
                self._active -= 1

    async def run(self, function: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
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
            self.finish(recorder, outcome, failed=failed)

    def capture(self) -> Callable:
        def decorate(function: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
            if not asyncio.iscoroutinefunction(function):
                raise TypeError("capture supports async callables")

            @functools.wraps(function)
            async def wrapped(*args: Any, **kwargs: Any) -> T:
                return await self.run(function, *args, **kwargs)

            return wrapped

        return decorate

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
                outcome = {"kind": "return", "value": encode(None, self.limits)}
            except Exception as exc:
                outcome = session.policy.exception(exc, self.limits, session.fail)
            return session.report(outcome)
        except ReplayDivergence:
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

    def asgi(self, app: Any) -> Any:
        from .adapters.asgi import CaptureMiddleware

        return CaptureMiddleware(app, self)

    async def replay_asgi(self, snapshot: Snapshot, app: Any) -> ReplayReport:
        from .adapters.asgi import replay_asgi

        return await replay_asgi(self, snapshot, app)
