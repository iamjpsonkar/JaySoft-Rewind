"""Explicit recorded observations of clocks, randomness, identifiers, and waits."""

import asyncio
import os
import random as _random
import time as _time
import uuid as _uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypeVar

from . import context
from .errors import RewindError
from .policy import sensitive
from .recorder import Recorder
from .replay import ReplaySession

T = TypeVar("T")
_EXCEPTIONS = {
    f"builtins.{kind.__name__}": kind
    for kind in (ValueError, TypeError, OverflowError, IndexError, KeyError)
}


def _replayed(active: ReplaySession, name: str, parameters: Any) -> Any:
    try:
        outcome = active.consume("value", f"rewind.sources.{name}", parameters)
        if outcome["kind"] == "return":
            return active.unpack(outcome["value"])
        exception = _EXCEPTIONS.get(outcome["type"])
        args = active.unpack(outcome["args"])
        if exception is None or type(args) is not tuple:
            active.fail("unsupported recorded source exception")
    except RewindError:
        active.fail("invalid recorded source observation")
    raise exception(*args)


def _observe(name: str, parameters: Any, factory: Callable[[], T]) -> T:
    active = context.current.get()
    if isinstance(active, ReplaySession):
        return _replayed(active, name, parameters)
    if not isinstance(active, Recorder) or active.sealed:
        return factory()
    slot = active.begin("value", f"rewind.sources.{name}", parameters)
    try:
        result = factory()
    except BaseException as exc:
        if f"{type(exc).__module__}.{type(exc).__qualname__}" not in _EXCEPTIONS:
            active.mark("source_exception_unsupported")
        active.finish(slot, active.raised(exc))
        raise
    active.finish(slot, active.returned(result))
    return result


class Sources:
    """Use these methods at application observation sites; stdlib calls stay unchanged.

    Replayed random operations reproduce recorded values, not RNG internal state.
    Populations and results must use supported codec types for complete capture.
    """

    def time(self) -> float:
        return _observe("time", {}, _time.time)

    def getenv(self, key: str, default: str | None = None) -> str | None:
        """Observe one environment value; known secret keys never enter recordings."""
        active = context.current.get()
        if isinstance(active, Recorder) and sensitive(key):
            active.mark("sensitive_environment_removed")
            return os.getenv(key, default)
        return _observe("getenv", {"key": key, "default": default}, lambda: os.getenv(key, default))

    def environ(self, key: str) -> str:
        """Observe a required environment value, including a missing-key exception."""
        active = context.current.get()
        if isinstance(active, Recorder) and sensitive(key):
            active.mark("sensitive_environment_removed")
            return os.environ[key]
        return _observe("environ", {"key": key}, lambda: os.environ[key])

    def sleep_sync(self, delay: float) -> None:
        """Record a synchronous wait; replay consumes it without waiting."""
        return _observe("sleep_sync", {"delay": delay}, lambda: _time.sleep(delay))

    def getrandbits(self, k: int) -> int:
        return _observe("getrandbits", {"k": k}, lambda: _random.getrandbits(k))

    def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
        return _observe(
            "randrange",
            {"start": start, "stop": stop, "step": step},
            lambda: _random.randrange(start, stop, step),
        )

    def time_ns(self) -> int:
        return _observe("time_ns", {}, _time.time_ns)

    def monotonic(self) -> float:
        return _observe("monotonic", {}, _time.monotonic)

    def monotonic_ns(self) -> int:
        return _observe("monotonic_ns", {}, _time.monotonic_ns)

    def perf_counter(self) -> float:
        return _observe("perf_counter", {}, _time.perf_counter)

    def perf_counter_ns(self) -> int:
        return _observe("perf_counter_ns", {}, _time.perf_counter_ns)

    def datetime_now(self, tz: timezone | None = None) -> datetime:
        if tz is not None and type(tz) is not timezone:
            raise TypeError("datetime_now supports only naive or fixed-offset timezones")
        parameters = {
            "timezone": None
            if tz is None
            else {
                "offset_us": tz.utcoffset(None) // timedelta(microseconds=1),
                "name": tz.tzname(None),
            }
        }
        return _observe("datetime_now", parameters, lambda: datetime.now(tz))

    def date_today(self) -> date:
        return _observe("date_today", {}, date.today)

    def uuid4(self) -> _uuid.UUID:
        return _observe("uuid4", {}, _uuid.uuid4)

    def random(self) -> float:
        return _observe("random", {}, _random.random)

    def randint(self, a: int, b: int) -> int:
        return _observe("randint", {"a": a, "b": b}, lambda: _random.randint(a, b))

    def uniform(self, a: float, b: float) -> float:
        return _observe("uniform", {"a": a, "b": b}, lambda: _random.uniform(a, b))

    def choice(self, population: Sequence[T]) -> T:
        return _observe("choice", {"population": population}, lambda: _random.choice(population))

    def sample(self, population: Sequence[T], k: int) -> list[T]:
        return _observe(
            "sample", {"population": population, "k": k}, lambda: _random.sample(population, k)
        )

    def shuffle(self, population: list[T]) -> None:
        def shuffled() -> list[T]:
            _random.shuffle(population)
            return population

        result = _observe("shuffle", {"population": population}, shuffled)
        population[:] = result

    async def sleep(self, delay: float, result: T | None = None) -> T | None:
        """Record a completed wait; replay preserves a scheduling yield without delay."""
        active = context.current.get()
        parameters = {"delay": delay, "result": result}
        if isinstance(active, ReplaySession):
            observed: T | None = _replayed(active, "sleep", parameters)
            await asyncio.sleep(0)
            return observed
        if not isinstance(active, Recorder) or active.sealed:
            return await asyncio.sleep(delay, result)
        slot = active.begin("value", "rewind.sources.sleep", parameters)
        try:
            observed = await asyncio.sleep(delay, result)
        except BaseException as exc:
            active.mark("source_sleep_interrupted")
            active.finish(slot, active.raised(exc))
            raise
        active.finish(slot, active.returned(observed))
        return observed
