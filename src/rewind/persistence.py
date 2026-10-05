"""Bounded, best-effort persistence of already sealed snapshot bytes."""

import math
import os
import threading
import time
from collections import deque
from contextvars import Context
from dataclasses import dataclass
from typing import Protocol

from .snapshot import Snapshot

_COUNTER_MAX = (1 << 63) - 1


class SnapshotStore(Protocol):
    def save(self, snapshot: Snapshot) -> object: ...


@dataclass(frozen=True)
class ShutdownReport:
    """State at return; an in-flight save cannot be cancelled by this writer."""

    drained: bool
    pending: int
    in_flight: bool
    dropped: int
    worker_alive: bool
    forked: bool = False


def _timeout(value: float) -> float:
    if (
        type(value) not in (int, float)
        or value < 0
        or value > threading.TIMEOUT_MAX
        or not math.isfinite(value)
    ):
        raise ValueError("timeout must be finite, nonnegative, and within threading.TIMEOUT_MAX")
    return float(value)


class BackgroundWriter:
    """One daemon worker with capacity including the active save.

    submit never performs storage I/O or waits for capacity. It briefly acquires
    a state lock shared only with bookkeeping, never with the store's save call.
    flush waits for idle, including work submitted while waiting. A successful
    flush means completion, not successful storage; consult saved/failed stats.
    """

    def __init__(
        self,
        store: SnapshotStore,
        *,
        max_items: int = 128,
        max_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if any(type(value) is not int or value <= 0 for value in (max_items, max_bytes)):
            raise ValueError("queue capacities must be positive integers")
        self._store = store
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._pid = os.getpid()
        self._condition = threading.Condition()
        self._queue: deque[Snapshot] = deque()
        self._pending_items = 0
        self._pending_bytes = 0
        self._peak_items = 0
        self._peak_bytes = 0
        self._in_flight = False
        self._closed = False
        self._counters = dict.fromkeys(("submitted", "saved", "failed", "dropped", "rejected"), 0)
        # Explicit empty context also avoids capture propagation on runtimes that
        # inherit ContextVars when starting a thread.
        self._thread = threading.Thread(
            target=Context().run, args=(self._run,), name="rewind-persistence", daemon=True
        )
        self._thread.start()

    def _increment(self, key: str, count: int = 1) -> None:
        self._counters[key] = min(_COUNTER_MAX, self._counters[key] + count)

    def submit(self, snapshot: Snapshot) -> bool:
        if os.getpid() != self._pid:
            return False
        with self._condition:
            # Exact types exclude mutable byte buffers and user-defined behavior
            # inside the lock. Schema validation belongs to sealing/storage.
            if (
                self._closed
                or type(snapshot) is not Snapshot
                or type(snapshot.raw) is not bytes
                or self._pending_items >= self._max_items
                or len(snapshot.raw) > self._max_bytes - self._pending_bytes
            ):
                self._increment("rejected")
                return False
            self._queue.append(snapshot)
            self._pending_items += 1
            self._pending_bytes += len(snapshot.raw)
            self._peak_items = max(self._peak_items, self._pending_items)
            self._peak_bytes = max(self._peak_bytes, self._pending_bytes)
            self._increment("submitted")
            self._condition.notify_all()
            return True

    def stats(self) -> dict[str, int | bool]:
        if os.getpid() != self._pid:
            # Never acquire synchronization primitives inherited across fork.
            return {
                **dict.fromkeys(self._counters, 0),
                "pending_items": 0,
                "pending_bytes": 0,
                "peak_pending_items": 0,
                "peak_pending_bytes": 0,
                "max_items": self._max_items,
                "max_bytes": self._max_bytes,
                "in_flight": False,
                "worker_alive": False,
                "closed": True,
                "forked": True,
            }
        with self._condition:
            return {
                **self._counters,
                "pending_items": self._pending_items,
                "pending_bytes": self._pending_bytes,
                "peak_pending_items": self._peak_items,
                "peak_pending_bytes": self._peak_bytes,
                "max_items": self._max_items,
                "max_bytes": self._max_bytes,
                "in_flight": self._in_flight,
                "worker_alive": self._thread.is_alive(),
                "closed": self._closed,
                "forked": False,
            }

    def flush(self, timeout: float = 5) -> bool:
        deadline = time.monotonic() + _timeout(timeout)
        if os.getpid() != self._pid:
            return False
        with self._condition:
            while self._pending_items:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, timeout: float = 5, drain: bool = True) -> ShutdownReport:
        deadline = time.monotonic() + _timeout(timeout)
        if type(drain) is not bool:
            raise ValueError("drain must be a boolean")
        if os.getpid() != self._pid:
            return ShutdownReport(False, 0, False, 0, False, forked=True)
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            if drain:
                while self._pending_items:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
            # Queued snapshots can be dropped. The active save owns its bytes
            # until it returns, even after the caller's shutdown deadline.
            self._increment("dropped", len(self._queue))
            self._pending_bytes -= sum(len(snapshot.raw) for snapshot in self._queue)
            self._pending_items -= len(self._queue)
            self._queue.clear()
            self._condition.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(max(0.0, deadline - time.monotonic()))
        with self._condition:
            return ShutdownReport(
                drained=self._pending_items == 0 and self._counters["dropped"] == 0,
                pending=self._pending_items,
                in_flight=self._in_flight,
                dropped=self._counters["dropped"],
                worker_alive=self._thread.is_alive(),
            )

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if not self._queue:
                    return
                snapshot = self._queue.popleft()
                self._in_flight = True
            try:
                self._store.save(snapshot)
            except BaseException:
                # Store failures, including thread-local SystemExit, must not
                # kill the worker or retain sensitive exceptions/tracebacks.
                result = "failed"
            else:
                result = "saved"
            with self._condition:
                self._increment(result)
                self._pending_items -= 1
                self._pending_bytes -= len(snapshot.raw)
                self._in_flight = False
                del snapshot
                self._condition.notify_all()
