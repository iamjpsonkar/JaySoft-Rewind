"""Explicit redis-py wrappers: ordered command and buffered pipeline observations."""

import importlib
import inspect
from typing import Any

from .. import context
from ..codecs import decode, dumps, encode
from ..errors import CaptureLimit, RewindError
from ..policy import sensitive
from ..recorder import Recorder
from ..replay import ReplaySession

# Deliberately excludes authentication, scripts, blocking calls, pub/sub, WATCH,
# administration, and helpers which execute multiple commands internally.
_METHODS = frozenset("""
get set setex psetex setnx getset getdel getex mget mset msetnx append strlen
incr incrby incrbyfloat decr decrby delete unlink exists expire pexpire expireat
pexpireat ttl pttl persist type rename renamenx hget hset hdel hexists hgetall
hkeys hvals hlen hmget hmset hincrby hincrbyfloat hsetnx lpush rpush lpushx rpushx
lpop rpop llen lindex lrange lset ltrim lrem linsert sadd srem smembers scard
sismember smismember spop srandmember sdiff sinter sunion zadd zrem zcard zcount
zincrby zrank zrevrank zscore zmscore zrange zrevrange zrangebyscore
zrevrangebyscore zremrangebyrank zremrangebyscore zpopmin zpopmax scan hscan
sscan zscan ping echo
""".split())
_EXCEPTIONS = frozenset({
    "ConnectionError", "TimeoutError", "ResponseError", "DataError", "BusyLoadingError",
    "AuthenticationError", "AuthorizationError", "ReadOnlyError", "NoScriptError",
})


def _active() -> Recorder | ReplaySession | None:
    active = context.current.get()
    return None if isinstance(active, Recorder) and active.sealed else active


def _allowed(name: str, args: tuple[Any, ...]) -> bool:
    if name in _METHODS:
        return True
    if name != "execute_command" or not args or type(args[0]) not in (str, bytes):
        return False
    command = args[0]
    if isinstance(command, bytes):
        try:
            command = command.decode("ascii")
        except UnicodeError:
            return False
    return command.lower() in _METHODS


def _exception(active: Recorder, exc: BaseException) -> dict[str, Any]:
    if type(exc).__module__ != "redis.exceptions" or type(exc).__name__ not in _EXCEPTIONS:
        active.mark("unsupported_redis_exception")
        # Never retain arbitrary exception messages from unsupported commands/classes.
        return {"kind": "exception", "type": "unavailable", "args": encode(None, active.limits)}
    return active.raised(exc)


def _raise(active: ReplaySession, outcome: dict[str, Any]) -> Any:
    name = outcome["type"].removeprefix("redis.exceptions.")
    if outcome["type"] != f"redis.exceptions.{name}" or name not in _EXCEPTIONS:
        active.fail("unsupported recorded Redis exception")
    args = decode(outcome["args"], active.limits)
    if type(args) is not tuple or any(type(arg) not in (str, int) for arg in args):
        active.fail("invalid recorded Redis exception")
    return getattr(importlib.import_module("redis.exceptions"), name)(*args)


def _pack(value: Any, active: Recorder) -> Any:
    """Preserve redis-py's bytes-key mappings and sets without changing core codecs."""
    count = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > active.limits.items or depth > active.limits.depth:
            raise CaptureLimit("Redis result exceeds structural limit")
        if type(item) is dict:
            pairs = []
            for key, val in item.items():
                label = key.decode("utf-8", errors="replace") if type(key) is bytes else key
                if type(label) is str and sensitive(label):
                    active.mark("sensitive_value_removed")
                    val = "[REDACTED]"
                pairs.append((visit(key, depth + 1), visit(val, depth + 1)))
            return ("dict", pairs)
        if type(item) in (list, tuple, set):
            values = [visit(v, depth + 1) for v in item]
            if type(item) is set:
                values.sort(key=lambda v: dumps(encode(v, active.limits)))
            return (type(item).__name__, values)
        if isinstance(item, BaseException):
            return ("exception", _exception(active, item))
        return ("scalar", item)

    return visit(value, 0)


def _unpack(value: Any, active: ReplaySession) -> Any:
    if type(value) is not tuple or len(value) != 2:
        active.fail("invalid recorded Redis value")
    kind, item = value
    if kind == "scalar":
        if item is None or type(item) in (str, bytes, bool, int, float):
            return item
    elif kind == "exception" and type(item) is dict:
        return _raise(active, item)
    elif kind in ("dict", "list", "tuple", "set") and type(item) is list:
        if kind == "dict":
            return {_unpack(k, active): _unpack(v, active) for k, v in item}
        values = [_unpack(v, active) for v in item]
        return {"list": list, "tuple": tuple, "set": set}[kind](values)
    active.fail("invalid recorded Redis value")


def _replay(active: ReplaySession, operation: str, dependency: str, data: Any) -> Any:
    outcome = active.consume(operation, dependency, data)
    try:
        if outcome["kind"] == "exception":
            exc = _raise(active, outcome)
        else:
            return _unpack(decode(outcome["value"], active.limits), active)
    except (RewindError, ValueError, TypeError, KeyError):
        active.fail("invalid recorded Redis outcome")
    raise exc


def _begin(active: Recorder | None, operation: str, dependency: str, data: Any) -> int | None:
    if active is None:
        return None
    try:
        return active.begin(operation, dependency, data)
    except Exception:
        active.mark("redis_input_capture_failed")
        return None


def _finish(active: Recorder | None, slot: int | None, result: Any) -> None:
    if active is not None and slot is not None and not active.sealed:
        try:
            active.finish(slot, active.returned(_pack(result, active)))
        except Exception:
            active.mark("redis_output_capture_failed")


def _unsupported(active: Recorder | ReplaySession | None) -> None:
    if isinstance(active, ReplaySession):
        active.fail("unsupported Redis operation")
    if isinstance(active, Recorder):
        active.mark("redis_operation_unsupported")


class RecordingRedis:
    """Wrap a synchronous redis.Redis; omit inner only for isolated replay."""

    def __init__(self, inner: Any = None, *, dependency: str = "redis") -> None:
        self.inner = inner
        self.dependency = dependency

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def command(*args: Any, **kwargs: Any) -> Any:
            active = _active()
            allowed = _allowed(name, args)
            if not allowed:
                _unsupported(active)
            data = {"method": name, "args": args, "kwargs": kwargs}
            if isinstance(active, ReplaySession):
                return _replay(active, "redis.command", self.dependency, data)
            recorder = active if isinstance(active, Recorder) and allowed else None
            slot = _begin(recorder, "redis.command", self.dependency, data)
            try:
                result = getattr(self.inner, name)(*args, **kwargs)
            except BaseException as exc:
                if recorder is not None:
                    recorder.finish(slot, _exception(recorder, exc))
                raise
            _finish(recorder, slot, result)
            return result

        return command

    def close(self) -> None:
        if not isinstance(_active(), ReplaySession) and self.inner is not None:
            self.inner.close()

    def __enter__(self) -> "RecordingRedis":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> "RecordingPipeline":
        return RecordingPipeline(self, transaction, shard_hint)


class AsyncRecordingRedis(RecordingRedis):
    """Wrap redis.asyncio.Redis; pipeline enqueue methods remain synchronous."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        async def command(*args: Any, **kwargs: Any) -> Any:
            active = _active()
            allowed = _allowed(name, args)
            if not allowed:
                _unsupported(active)
            data = {"method": name, "args": args, "kwargs": kwargs}
            if isinstance(active, ReplaySession):
                return _replay(active, "redis.command", self.dependency, data)
            recorder = active if isinstance(active, Recorder) and allowed else None
            slot = _begin(recorder, "redis.command", self.dependency, data)
            try:
                result = await getattr(self.inner, name)(*args, **kwargs)
            except BaseException as exc:
                if recorder is not None:
                    recorder.finish(slot, _exception(recorder, exc))
                raise
            _finish(recorder, slot, result)
            return result

        return command

    async def aclose(self) -> None:
        if not isinstance(_active(), ReplaySession) and self.inner is not None:
            await self.inner.aclose()

    async def __aenter__(self) -> "AsyncRecordingRedis":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    def pipeline(
        self, transaction: bool = True, shard_hint: Any = None
    ) -> "AsyncRecordingPipeline":
        return AsyncRecordingPipeline(self, transaction, shard_hint)


class RecordingPipeline:
    def __init__(self, parent: RecordingRedis, transaction: bool, shard_hint: Any) -> None:
        self.parent = parent
        self.transaction = transaction
        self.shard_hint = shard_hint
        self.inner: Any = None
        self.commands: list[Any] = []
        self.scope: Any = None
        self.unsupported = False
        self.bytes_used = 0
        self.queued = False

    def _inner(self) -> Any:
        if self.inner is None:
            self.inner = self.parent.inner.pipeline(
                transaction=self.transaction, shard_hint=self.shard_hint
            )
        return self.inner

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def queue(*args: Any, **kwargs: Any) -> Any:
            active = _active()
            if not self.queued:
                self.scope = active
            self.queued = True
            if not _allowed(name, args):
                self.unsupported = True
                _unsupported(active)
            if active is not None and not self.unsupported:
                data = {"method": name, "args": args, "kwargs": kwargs}
                try:
                    if active is not None:
                        packed = encode(data, active.limits)
                        self.bytes_used += len(dumps(packed))
                        if (self.bytes_used > active.limits.snapshot_bytes // 2
                                or len(self.commands) >= active.limits.items):
                            raise CaptureLimit("Redis pipeline exceeds limits")
                        data = decode(packed, active.limits)
                    self.commands.append(data)
                except Exception:
                    self.unsupported = True
                    _unsupported(active)
            if isinstance(active, ReplaySession):
                return self
            result = getattr(self._inner(), name)(*args, **kwargs)
            return self if result is self.inner else result

        return queue

    def _prepare(self, raise_on_error: bool) -> tuple[Any, dict[str, Any]]:
        active = _active()
        if self.unsupported or (self.queued and self.scope is not active):
            _unsupported(active)
        data = {"transaction": self.transaction, "shard_hint": self.shard_hint,
                "commands": self.commands, "raise_on_error": raise_on_error}
        return active, data

    def _clear(self) -> None:
        self.commands = []
        self.scope = None
        self.unsupported = False
        self.bytes_used = 0
        self.queued = False

    def execute(self, raise_on_error: bool = True) -> Any:
        active, data = self._prepare(raise_on_error)
        try:
            if isinstance(active, ReplaySession):
                return _replay(active, "redis.pipeline", self.parent.dependency, data)
            recorder = active if isinstance(active, Recorder) and not self.unsupported else None
            slot = _begin(recorder, "redis.pipeline", self.parent.dependency, data)
            try:
                result = self._inner().execute(raise_on_error=raise_on_error)
            except BaseException as exc:
                if recorder is not None:
                    recorder.finish(slot, _exception(recorder, exc))
                raise
            _finish(recorder, slot, result)
            return result
        finally:
            self._clear()

    def reset(self) -> None:
        self._clear()
        if not isinstance(_active(), ReplaySession) and self.inner is not None:
            self.inner.reset()

    def __enter__(self) -> "RecordingPipeline":
        return self

    def __exit__(self, *args: Any) -> None:
        self.reset()


class AsyncRecordingPipeline(RecordingPipeline):
    async def execute(self, raise_on_error: bool = True) -> Any:
        active, data = self._prepare(raise_on_error)
        try:
            if isinstance(active, ReplaySession):
                return _replay(active, "redis.pipeline", self.parent.dependency, data)
            recorder = active if isinstance(active, Recorder) and not self.unsupported else None
            slot = _begin(recorder, "redis.pipeline", self.parent.dependency, data)
            try:
                result = await self._inner().execute(raise_on_error=raise_on_error)
            except BaseException as exc:
                if recorder is not None:
                    recorder.finish(slot, _exception(recorder, exc))
                raise
            _finish(recorder, slot, result)
            return result
        finally:
            self._clear()

    async def reset(self) -> None:  # type: ignore[override]
        self._clear()
        if not isinstance(_active(), ReplaySession) and self.inner is not None:
            result = self.inner.reset()
            if inspect.isawaitable(result):
                await result

    async def __aenter__(self) -> "AsyncRecordingPipeline":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.reset()
