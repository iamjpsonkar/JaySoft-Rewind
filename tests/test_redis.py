"""Redis adapter unit cases and opt-in real-server Unix-socket conformance."""

import os
import shutil
import subprocess
import tempfile
import time

import pytest

from rewind import CapturePolicy, Limits
from rewind.adapters.redis import AsyncRecordingRedis, RecordingRedis
from rewind.codecs import decode

redis = pytest.importorskip("redis")


class FakeRedis:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []
        self.closed = False

    def __getattr__(self, name):
        def command(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            result = next(self.results)
            if isinstance(result, BaseException):
                raise result
            return result
        return command

    def pipeline(self, transaction=True, shard_hint=None):
        return FakePipeline(self, transaction)

    def close(self):
        self.closed = True


class FakePipeline:
    def __init__(self, parent, transaction):
        self.parent = parent
        self.transaction = transaction
        self.commands = []

    def __getattr__(self, name):
        def enqueue(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self
        return enqueue

    def execute(self, raise_on_error=True):
        self.parent.calls.append(
            ("pipeline", self.transaction, self.commands.copy(), raise_on_error)
        )
        self.reset()
        result = next(self.parent.results)
        if isinstance(result, BaseException):
            raise result
        return result

    def reset(self):
        self.commands.clear()


class AsyncFakeRedis(FakeRedis):
    def __getattr__(self, name):
        command = super().__getattr__(name)

        async def call(*args, **kwargs):
            return command(*args, **kwargs)
        return call

    def pipeline(self, transaction=True, shard_hint=None):
        return AsyncFakePipeline(self, transaction)

    async def aclose(self):
        self.closed = True


class AsyncFakePipeline(FakePipeline):
    async def execute(self, raise_on_error=True):
        self.parent.calls.append(
            ("pipeline", self.transaction, self.commands.copy(), raise_on_error)
        )
        await self.reset()
        return next(self.parent.results)

    async def reset(self):
        self.commands.clear()


def saved(recorder):
    ids = recorder.store.ids()
    assert len(ids) == 1
    return recorder.store.load(ids[0])


@pytest.mark.parametrize("result", [
    None, True, 19, 1.25, b"bytes", "text", [b"a", None],
    {b"field": b"value"}, {"field": "value"}, {b"a", b"b"},
    (1, [(b"member", 1.0)]),
])
async def test_result_types_survive_offline_replay(recorder, result):
    inner = FakeRedis([result])
    client = RecordingRedis(inner)

    async def operation():
        value = client.get("fixture")
        assert type(value) is type(result)
        assert value == result
        return "ok"

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete, snapshot.data["capture"]
    client.inner = None
    report = await recorder.replay(snapshot, operation)
    assert report.reproduced, report
    assert len(inner.calls) == 1


@pytest.mark.parametrize("transaction", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_ordered_pipeline_and_reuse(recorder, transaction, asynchronous):
    cls, fake = (
        (AsyncRecordingRedis, AsyncFakeRedis) if asynchronous else (RecordingRedis, FakeRedis)
    )
    inner = fake([[True, b"a"], [b"b"]])
    client = cls(inner)

    async def operation():
        pipe = client.pipeline(transaction=transaction)
        assert pipe.set("key", "a").get("key") is pipe
        first = await pipe.execute() if asynchronous else pipe.execute()
        pipe.get("key")
        second = await pipe.execute() if asynchronous else pipe.execute()
        if asynchronous:
            await pipe.reset()
        else:
            pipe.reset()
        return first, second

    assert await recorder.run(operation) == ([True, b"a"], [b"b"])
    snapshot = saved(recorder)
    assert snapshot.complete
    assert [item["operation"] for item in snapshot.data["interactions"]] == [
        "redis.pipeline", "redis.pipeline",
    ]
    client.inner = None
    assert (await recorder.replay(snapshot, operation)).reproduced
    assert len(inner.calls) == 2


async def test_async_commands_and_context_manager(recorder):
    inner = AsyncFakeRedis([True, b"value"])
    client = AsyncRecordingRedis(inner)

    async def operation():
        async with client:
            assert await client.set("key", "value", ex=60)
            return await client.get("key")

    await recorder.run(operation)
    assert inner.closed
    client.inner = None
    assert (await recorder.replay(saved(recorder), operation)).reproduced


@pytest.mark.parametrize(
    "exception", [redis.ConnectionError, redis.TimeoutError, redis.ResponseError]
)
async def test_permitted_exception_identity_and_args(recorder, exception):
    client = RecordingRedis(FakeRedis([exception("synthetic failure")]))

    async def operation():
        try:
            client.get("fixture")
        except exception as exc:
            return type(exc).__name__, exc.args

    await recorder.run(operation)
    client.inner = None
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_pipeline_embedded_errors(recorder):
    client = RecordingRedis(FakeRedis([[redis.ResponseError("fixture"), b"ok"]]))

    async def operation():
        with client.pipeline(transaction=False) as pipe:
            pipe.get("a").get("b")
            errors, value = pipe.execute(raise_on_error=False)
            assert type(errors) is redis.ResponseError
            return errors.args, value

    await recorder.run(operation)
    client.inner = None
    assert (await recorder.replay(saved(recorder), operation)).reproduced


@pytest.mark.parametrize("change", ["key", "kwargs", "dependency", "transaction", "order"])
async def test_changed_inputs_diverge_without_live_calls(recorder, change):
    inner = FakeRedis([[b"a", b"b"]])
    client = RecordingRedis(inner)
    changed = False

    async def operation():
        with client.pipeline(transaction=not (changed and change == "transaction")) as pipe:
            key = "changed" if changed and change == "key" else "a"
            kwargs = {"ex": 2 if changed and change == "kwargs" else 1}
            if changed and change == "order":
                pipe.get("b").set(key, "x", **kwargs)
            else:
                pipe.set(key, "x", **kwargs).get("b")
            return pipe.execute()

    await recorder.run(operation)
    changed = True
    if change == "dependency":
        client.dependency = "different"
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"
    assert len(inner.calls) == 1


@pytest.mark.parametrize("name,args", [
    ("auth", ("PASSWORD_SECRET",)), ("eval", ("LUA_SECRET", 0)),
    ("blpop", ("BLOCK_SECRET",)), ("pubsub", ()),
    ("execute_command", ("AUTH", "PASSWORD_SECRET")),
])
async def test_unsupported_commands_pass_through_without_payload_capture(recorder, name, args):
    inner = FakeRedis(["ok"])
    client = RecordingRedis(inner)

    async def operation():
        assert getattr(client, name)(*args) == "ok"
        return None

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert snapshot.data["interactions"] == []
    assert all(secret not in repr(snapshot.data) for secret in args if isinstance(secret, str))
    assert len(inner.calls) == 1


async def test_raw_execute_command(recorder):
    client = RecordingRedis(FakeRedis([b"value"]))

    async def operation():
        return client.execute_command(b"GET", b"key")

    await recorder.run(operation)
    client.inner = None
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_default_policy_excludes_values_and_exceptions(recorder):
    recorder.policy = CapturePolicy()
    client = RecordingRedis(FakeRedis([redis.ResponseError("PRIVATE_FAILURE")]))

    async def operation():
        try:
            client.get("PRIVATE_KEY")
        except redis.ResponseError:
            return None

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert "PRIVATE" not in repr(snapshot.data)


async def test_sensitive_hash_fields_removed(recorder):
    client = RecordingRedis(FakeRedis([{b"password": b"PRIVATE"}]))

    async def operation():
        assert client.hgetall("fixture") == {b"password": b"PRIVATE"}

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert "PRIVATE" not in repr(snapshot.data)
    assert "sensitive_value_removed" in snapshot.data["capture"]["ineligible_reasons"]


async def test_pipeline_queue_limit_preserves_application(recorder):
    recorder.limits = Limits(items=64)
    inner = FakeRedis([[True] * 65])
    client = RecordingRedis(inner)

    async def operation():
        pipe = client.pipeline()
        for _ in range(65):
            pipe.set("x", "y")
        assert pipe.execute() == [True] * 65

    await recorder.run(operation)
    assert not saved(recorder).complete
    assert len(inner.calls[0][2]) == 65


async def test_unrecognized_exception_payload_not_recorded(recorder):
    client = RecordingRedis(FakeRedis([RuntimeError("PRIVATE_ERROR")]))

    async def operation():
        with pytest.raises(RuntimeError):
            client.get("x")

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert "PRIVATE_ERROR" not in repr(snapshot.data)


async def test_pipeline_scope_changes_are_ineligible(recorder):
    inner = FakeRedis([[b"x"]])
    pipe = RecordingRedis(inner).pipeline()
    pipe.get("key")

    async def operation():
        assert pipe.execute() == [b"x"]

    await recorder.run(operation)
    assert not saved(recorder).complete


@pytest.fixture
def redis_socket():
    if os.environ.get("REWIND_REDIS_CONFORMANCE") != "1":
        pytest.skip("set REWIND_REDIS_CONFORMANCE=1 for an isolated local Redis server")
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.fail("redis-server is required for conformance")
    directory = tempfile.TemporaryDirectory(prefix="rwredis-", dir="/tmp")
    socket = directory.name + "/redis.sock"
    process = subprocess.Popen([
        executable, "--port", "0", "--unixsocket", socket,
        "--save", "", "--appendonly", "no", "--loglevel", "warning",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if os.path.exists(socket):
                break
            if process.poll() is not None:
                pytest.fail("isolated Redis server failed to start")
            time.sleep(0.01)
        else:
            pytest.fail("isolated Redis server did not create its socket")
        yield socket
    finally:
        process.terminate()
        process.wait(timeout=5)
        directory.cleanup()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("decoded", [False, True])
async def test_real_redis_conformance(recorder, redis_socket, asynchronous, decoded):
    module = redis.asyncio if asynchronous else redis
    inner = module.Redis(unix_socket_path=redis_socket, decode_responses=decoded)
    client = AsyncRecordingRedis(inner) if asynchronous else RecordingRedis(inner)

    async def call(name, *args, **kwargs):
        result = getattr(client, name)(*args, **kwargs)
        return await result if asynchronous else result

    async def operation():
        await call("set", "fixture", "value")
        value = await call("get", "fixture")
        assert type(value) is (str if decoded else bytes)
        await call("hset", "hash", mapping={"field": "value"})
        mapping = await call("hgetall", "hash")
        assert type(next(iter(mapping))) is (str if decoded else bytes)
        await call("sadd", "members", "a", "b")
        members = await call("smembers", "members")
        assert type(members) is set
        for transaction in (False, True):
            pipe = client.pipeline(transaction=transaction)
            pipe.get("fixture").hgetall("fixture").get("missing")
            results = await pipe.execute(False) if asynchronous else pipe.execute(False)
            assert results[0] == value and type(results[1]) is redis.ResponseError
            assert results[2] is None
        return value, sorted(members)

    try:
        await recorder.run(operation)
        snapshot = saved(recorder)
        assert snapshot.complete, snapshot.data["capture"]
        # Remove the actual client entirely before replay: any fallback fails.
        client.inner = None
        report = await recorder.replay(snapshot, operation)
        assert report.reproduced, report
        assert decode(snapshot.data["outcome"]["value"], recorder.limits)
    finally:
        if asynchronous:
            await inner.aclose()
        else:
            inner.close()


async def test_child_task_pipeline_queue_is_ineligible(recorder):
    import asyncio

    client = AsyncRecordingRedis(AsyncFakeRedis([[b"value"]]))

    async def operation():
        pipe = client.pipeline()

        async def enqueue():
            pipe.get("key")

        await asyncio.create_task(enqueue())
        return await pipe.execute()

    assert await recorder.run(operation) == [b"value"]
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert "child_task_unsupported" in snapshot.data["capture"]["ineligible_reasons"]


async def test_async_legacy_close_replay_does_not_touch_client(recorder):
    client = AsyncRecordingRedis(AsyncFakeRedis([b"value"]))

    async def operation():
        value = await client.get("key")
        await client.close()
        return value

    await recorder.run(operation)
    assert client.inner.closed
    client.inner = None
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_unsupported_exception_args_are_ineligible(recorder):
    client = RecordingRedis(FakeRedis([redis.ResponseError({"PRIVATE": "fixture"})]))

    async def operation():
        with pytest.raises(redis.ResponseError):
            client.get("key")

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert "PRIVATE" not in repr(snapshot.data)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_pipeline_enqueue_validation_failure_is_ineligible(recorder, asynchronous):
    inner = redis.asyncio.Redis() if asynchronous else redis.Redis()
    client = AsyncRecordingRedis(inner) if asynchronous else RecordingRedis(inner)

    async def operation():
        pipe = client.pipeline()
        with pytest.raises(redis.DataError):
            pipe.set("key", "value", ex=1.5)
        result = await pipe.execute() if asynchronous else pipe.execute()
        assert result == []
        return True

    try:
        assert await recorder.run(operation)
        snapshot = saved(recorder)
        assert not snapshot.complete
        assert "redis_pipeline_enqueue_failed" in snapshot.data["capture"]["ineligible_reasons"]
    finally:
        if asynchronous:
            await inner.aclose()
        else:
            inner.close()


@pytest.mark.parametrize("command,args,kwargs", [
    ("hset", ("hash", "customer_id", "PRIVATE_VALUE"), {}),
    ("hset", ("hash", b"customer_id", b"PRIVATE_VALUE"), {}),
    ("hset", ("hash",), {"key": "customer_id", "value": "PRIVATE_VALUE"}),
    ("hset", ("hash",), {"items": ["customer_id", "PRIVATE_VALUE"]}),
    ("hset", ("hash", None, None, {"customer_id": "PRIVATE_VALUE"}), {}),
    ("hmset", ("hash", {"customer_id": "PRIVATE_VALUE"}), {}),
    ("hget", ("hash", "customer_id"), {}),
    ("hmget", ("hash", ["public", "customer_id"]), {}),
    ("hdel", ("hash", "public", "customer_id"), {}),
    ("hexists", ("hash", "customer_id"), {}),
    ("hincrby", ("hash", "customer_id", 1), {}),
    ("hincrbyfloat", ("hash", "customer_id", 1.1), {}),
    ("hsetnx", ("hash", "customer_id", "PRIVATE_VALUE"), {}),
    ("hvals", ("hash",), {}),
    ("execute_command", ("HSET", "hash", "customer_id", "PRIVATE_VALUE"), {}),
    ("execute_command", (b"HSET", b"hash", b"customer_id", b"PRIVATE_VALUE"), {}),
    ("execute_command", ("HGET", "hash", "customer_id"), {}),
    ("execute_command", ("HMGET", "hash", "public", "customer_id"), {}),
])
@pytest.mark.parametrize("pipeline", [False, True])
async def test_positional_hash_fields_obey_custom_policy(
    recorder, command, args, kwargs, pipeline,
):
    from dataclasses import replace

    recorder.policy = replace(CapturePolicy.synthetic(), redacted_keys=("customer_id",))
    result = ["PRIVATE_VALUE"] if pipeline else "PRIVATE_VALUE"
    inner = FakeRedis([result])
    client = RecordingRedis(inner)

    async def operation():
        target = client.pipeline() if pipeline else client
        returned = getattr(target, command)(*args, **kwargs)
        assert (target.execute() if pipeline else returned) == result

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"PRIVATE_VALUE" not in snapshot.raw
    assert snapshot.data["interactions"] == []
    assert "sensitive_redis_fields_removed" in snapshot.data["capture"]["ineligible_reasons"]


@pytest.mark.parametrize("pipeline", [False, True])
async def test_async_hash_field_privacy(recorder, pipeline):
    inner = AsyncFakeRedis([[b"PRIVATE_VALUE"]] if pipeline else [b"PRIVATE_VALUE"])
    client = AsyncRecordingRedis(inner)

    async def operation():
        if pipeline:
            pipe = client.pipeline()
            pipe.hget("hash", b"password")
            assert await pipe.execute() == [b"PRIVATE_VALUE"]
        else:
            assert await client.hget("hash", b"password") == b"PRIVATE_VALUE"

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"PRIVATE_VALUE" not in snapshot.raw


async def test_hash_policy_failure_does_not_change_application(recorder):
    class BrokenPolicy(CapturePolicy):
        def is_sensitive(self, key):
            raise RuntimeError("policy failure")

    recorder.policy = BrokenPolicy()
    client = RecordingRedis(FakeRedis([b"value"]))

    async def operation():
        assert client.hget("hash", "field") == b"value"

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert "redis_field_policy_failed" in snapshot.data["capture"]["ineligible_reasons"]
