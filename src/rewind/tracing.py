"""Opt-in, bounded function diagnostics, independent of required replay observations."""

import functools
import inspect
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from . import context
from .codecs import dumps

F = TypeVar("F", bound=Callable[..., Any])
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:\-]{0,127}\Z")


@dataclass(frozen=True)
class TraceConfig:
    """No tracing by default; diagnostic limits never enlarge replay limits."""

    enabled: bool = False
    max_events: int = 256
    max_bytes: int = 65536
    max_depth: int = 32

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        for name in ("max_events", "max_bytes", "max_depth"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError("diagnostic limits must be positive integers")
        if self.max_events > 10000 or self.max_bytes > 1024 * 1024 or self.max_depth > 128:
            raise ValueError("diagnostic limits exceed supported maxima")


class TraceBuffer:
    """Thread-safe optional-event ring. No required recording data is referenced."""

    def __init__(self, config: TraceConfig, *, byte_limit: int | None = None) -> None:
        if byte_limit is not None and (type(byte_limit) is not int or byte_limit < 0):
            raise ValueError("byte_limit must be a nonnegative integer")
        self.config = config
        self.byte_limit = (
            min(config.max_bytes, byte_limit) if byte_limit is not None else config.max_bytes
        )
        self._events: deque[tuple[dict[str, Any], int]] = deque()
        self._bytes = 0
        self._sequence = 0
        self._span = 0
        self._dropped = 0
        self._closed = False
        self._started = time.perf_counter_ns()
        self._lock = threading.Lock()
        self._pid = os.getpid()

    def start(self) -> int | None:
        if os.getpid() != self._pid:
            return None
        with self._lock:
            if self._closed or not self.config.enabled:
                return None
            self._span += 1
            return self._span

    def drop(self) -> None:
        if os.getpid() != self._pid:
            return
        with self._lock:
            if not self._closed:
                self._dropped += 1

    def emit(
        self,
        *,
        span_id: int,
        parent_id: int | None,
        kind: str,
        phase: str,
        name: str,
        elapsed_ns: int | None = None,
        exception_type: str | None = None,
    ) -> None:
        if os.getpid() != self._pid:
            return
        with self._lock:
            if self._closed or not self.config.enabled:
                return
            self._sequence += 1
            event = {
                "sequence": self._sequence,
                "span_id": span_id,
                "parent_id": parent_id,
                "kind": kind,
                "phase": phase,
                "name": name,
                "offset_ns": max(0, time.perf_counter_ns() - self._started),
                "elapsed_ns": elapsed_ns,
                "exception_type": exception_type,
            }
            size = len(dumps(event)) + 1
            if size > self.byte_limit:
                self._dropped += 1
                return
            while self._events and (
                len(self._events) >= self.config.max_events or self._bytes + size > self.byte_limit
            ):
                _, previous_size = self._events.popleft()
                self._bytes -= previous_size
                self._dropped += 1
            self._events.append((event, size))
            self._bytes += size

    def finish(self, *, byte_budget: int | None = None) -> dict[str, Any] | None:
        """Close/detach the ring; trim optional events to fit remaining artifact space.

        Return None only if even an empty diagnostic summary will not fit. The
        caller must reserve JSON key overhead when computing byte_budget.
        """
        if os.getpid() != self._pid:
            return None
        if byte_budget is not None and (type(byte_budget) is not int or byte_budget < 0):
            raise ValueError("byte_budget must be a nonnegative integer")
        with self._lock:
            if self._closed:
                return None
            self._closed = True
            if byte_budget is not None:
                while self._events:
                    overhead = len(dumps({"version": 1, "events": [], "dropped": self._dropped}))
                    if self._bytes + overhead <= byte_budget:
                        break
                    _, size = self._events.popleft()
                    self._bytes -= size
                    self._dropped += 1
            result: dict[str, Any] = {
                "version": 1,
                "events": [dict(event) for event, _ in self._events],
                "dropped": self._dropped,
            }
            self._events.clear()
            self._bytes = 0
            if byte_budget is not None and len(dumps(result)) > byte_budget:
                return None
            return result

    def clear(self) -> None:
        """Release retained events and permanently stop recording."""
        if os.getpid() != self._pid:
            return
        with self._lock:
            self._closed = True
            self._events.clear()
            self._bytes = 0


@dataclass(frozen=True)
class _Frame:
    buffer: TraceBuffer
    span_id: int | None
    depth: int


_stack: ContextVar[_Frame | None] = ContextVar("rewind_diagnostic_span", default=None)


def _buffer() -> TraceBuffer | None:
    active = context.current.get()
    if active is None or getattr(active, "sealed", False):
        return None
    buffer = getattr(active, "diagnostics", None)
    if not isinstance(buffer, TraceBuffer) or not buffer.config.enabled:
        return None
    return buffer


def _name(value: str) -> str:
    if type(value) is not str or not _NAME.fullmatch(value):
        raise ValueError("trace names must be static identifiers of at most 128 characters")
    return value


class span:
    """Explicit sync/async context manager for a named, payload-free diagnostic span."""

    def __init__(self, name: str, *, _kind: str = "span") -> None:
        self.name = _name(name)
        self.kind = _kind
        self._buffer: TraceBuffer | None = None
        self._id: int | None = None
        self._parent: int | None = None
        self._started = 0
        self._token: Token[_Frame | None] | None = None
        self._entered = False

    def __enter__(self) -> "span":
        if self._entered:
            raise RuntimeError("a span context manager cannot be entered twice")
        try:
            return self._enter()
        except Exception:
            if self._buffer is not None:
                try:
                    self._buffer.drop()
                except Exception:
                    pass
            return self

    def _enter(self) -> "span":
        if self._entered:
            raise RuntimeError("a span context manager cannot be entered twice")
        self._entered = True
        buffer = _buffer()
        if buffer is None:
            return self
        self._buffer = buffer
        frame = _stack.get()
        same_buffer = frame is not None and frame.buffer is buffer
        depth = frame.depth + 1 if same_buffer and frame is not None else 1
        self._parent = frame.span_id if same_buffer and frame is not None else None
        if depth > buffer.config.max_depth:
            buffer.drop()
            self._token = _stack.set(_Frame(buffer, None, depth))
            return self
        self._id = buffer.start()
        if self._id is None:
            return self
        self._started = time.perf_counter_ns()
        self._token = _stack.set(_Frame(buffer, self._id, depth))
        buffer.emit(
            span_id=self._id, parent_id=self._parent, kind=self.kind, phase="enter", name=self.name
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self._buffer is None:
                return
            if self._id is None:
                self._buffer.drop()
                return
            exception_type = None
            if exc_type is not None:
                candidate = exc_type.__name__
                exception_type = candidate if _NAME.fullmatch(candidate) else "Exception"
            self._buffer.emit(
                span_id=self._id,
                parent_id=self._parent,
                kind=self.kind,
                phase="exception" if exc_type is not None else "return",
                name=self.name,
                elapsed_ns=max(0, time.perf_counter_ns() - self._started),
                exception_type=exception_type,
            )
        except Exception:
            if self._buffer is not None:
                try:
                    self._buffer.drop()
                except Exception:
                    pass
        finally:
            if self._token is not None:
                try:
                    _stack.reset(self._token)
                except (ValueError, RuntimeError):
                    pass
            self._token = None
            self._buffer = None

    async def __aenter__(self) -> "span":
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.__exit__(exc_type, exc, tb)


@overload
def trace(function: F, *, name: str | None = None) -> F: ...


@overload
def trace(function: None = None, *, name: str | None = None) -> Callable[[F], F]: ...


def trace(function: F | None = None, *, name: str | None = None) -> Any:
    """Trace declared sync/async functions; generator functions are explicitly unsupported."""

    def decorate(target: F) -> Any:
        if inspect.isgeneratorfunction(target) or inspect.isasyncgenfunction(target):
            raise TypeError("generator functions require explicit spans around consumed work")
        label = _name(name if name is not None else target.__qualname__.replace("<locals>.", ""))
        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def asynchronous(*args: Any, **kwargs: Any) -> Any:
                if _buffer() is None:
                    return await target(*args, **kwargs)
                with span(label, _kind="function"):
                    return await target(*args, **kwargs)

            return asynchronous

        @functools.wraps(target)
        def synchronous(*args: Any, **kwargs: Any) -> Any:
            if _buffer() is None:
                return target(*args, **kwargs)
            with span(label, _kind="function"):
                return target(*args, **kwargs)

        return synchronous

    return decorate if function is None else decorate(function)
