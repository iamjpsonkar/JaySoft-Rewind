import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from rewind import CapturePolicy, Limits
from rewind.codecs import decode, encode
from rewind.errors import CaptureLimit, InvalidSnapshot
from rewind.sources import Sources


def saved(recorder):
    return recorder.store.load(recorder.store.ids()[0])


async def test_sources_replay_exact_values_and_skip_factories(recorder, monkeypatch):
    sources = Sources()

    async def operation():
        return [
            sources.time(),
            sources.time_ns(),
            sources.monotonic(),
            sources.monotonic_ns(),
            sources.perf_counter(),
            sources.perf_counter_ns(),
            sources.datetime_now(UTC),
            sources.datetime_now(),
            sources.date_today(),
            sources.uuid4(),
            sources.random(),
            sources.randint(-10, 20),
            sources.uniform(1.5, 99.0),
            sources.choice(["a", "b"]),
            sources.sample([1, 2, 3], 2),
        ]

    expected = await recorder.run(operation)
    artifact = saved(recorder)
    assert artifact.complete
    assert isinstance(expected[6], datetime)
    assert isinstance(expected[8], date)
    assert isinstance(expected[9], UUID)

    def forbidden(*args, **kwargs):
        pytest.fail("live observation used during replay")

    # Do not patch time.monotonic globally: the event loop and recorder use it.
    from types import SimpleNamespace

    import rewind.sources as module

    monkeypatch.setattr(
        module,
        "_time",
        SimpleNamespace(
            **{
                name: forbidden
                for name in (
                    "time",
                    "time_ns",
                    "monotonic",
                    "monotonic_ns",
                    "perf_counter",
                    "perf_counter_ns",
                )
            }
        ),
    )
    monkeypatch.setattr(module, "_uuid", SimpleNamespace(uuid4=forbidden))
    monkeypatch.setattr(
        module,
        "_random",
        SimpleNamespace(
            **{name: forbidden for name in ("random", "randint", "uniform", "choice", "sample")}
        ),
    )
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=forbidden))
    monkeypatch.setattr(module, "date", SimpleNamespace(today=forbidden))
    assert (await recorder.replay(artifact, operation)).reproduced


async def test_repeated_clock_and_uuid_observations_remain_distinct(recorder, monkeypatch):
    from types import SimpleNamespace

    import rewind.sources as module

    observations = iter([11.0, 22.0])
    monkeypatch.setattr(module, "_time", SimpleNamespace(time=lambda: next(observations)))
    sources = Sources()

    async def operation():
        return [sources.time(), sources.time(), sources.uuid4(), sources.uuid4()]

    result = await recorder.run(operation)
    assert result[:2] == [11.0, 22.0]
    assert result[2] != result[3]
    assert (await recorder.replay(saved(recorder), operation)).reproduced


@pytest.mark.parametrize(
    "method,before,after",
    [
        ("randint", (1, 3), (1, 4)),
        ("uniform", (1.0, 3.0), (1.0, 4.0)),
        ("choice", (["a", "b"],), (["a", "c"],)),
        ("shuffle", ([1, 2, 3],), ([1, 2, 4],)),
        ("sample", ([1, 2, 3], 2), ([1, 2, 3], 1)),
        ("datetime_now", (UTC,), (timezone(timedelta(hours=1)),)),
    ],
)
async def test_changed_source_arguments_diverge_even_when_caught(recorder, method, before, after):
    source = getattr(Sources(), method)
    arguments = before

    async def operation():
        try:
            source(*arguments)
        except Exception:
            pass

    await recorder.run(operation)
    arguments = after
    report = await recorder.replay(saved(recorder), operation)
    assert report.status == "diverged"
    assert "interaction 1" in report.detail


async def test_shuffle_replays_mutation_without_calling_rng(recorder, monkeypatch):
    import rewind.sources as module

    sources = Sources()

    async def operation():
        population = list(range(20))
        reference = population
        assert sources.shuffle(population) is None
        assert population is reference
        return population

    result = await recorder.run(operation)
    assert sorted(result) == list(range(20))

    def forbidden(*args):
        pytest.fail("shuffle RNG used during replay")

    monkeypatch.setattr(module._random, "shuffle", forbidden)
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_sleep_replay_yields_without_requested_delay(recorder, monkeypatch):
    from types import SimpleNamespace

    import rewind.sources as module

    sources = Sources()
    delays = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, result=None):
        delays.append(delay)
        await real_sleep(0)
        return result

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=fake_sleep))

    async def operation():
        return await sources.sleep(3600, "completed")

    assert await recorder.run(operation) == "completed"
    assert delays == [3600]
    delays.clear()
    assert (await recorder.replay(saved(recorder), operation)).reproduced
    assert delays == [0]


async def test_source_failures_are_observations(recorder):
    sources = Sources()

    async def operation():
        try:
            sources.choice([])
        except IndexError:
            return "handled"

    await recorder.run(operation)
    assert saved(recorder).complete
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_default_policy_keeps_source_capture_ineligible(recorder):
    recorder.policy = CapturePolicy()

    async def operation():
        return Sources().uuid4()

    await recorder.run(operation)
    assert not saved(recorder).complete


@pytest.mark.parametrize(
    "value",
    [
        UUID("00000000-0000-4000-8000-000000000001"),
        date(2024, 2, 29),
        datetime(2024, 11, 3, 1, 30, fold=1),
        datetime(
            2024, 2, 29, 12, 34, 56, 789, tzinfo=timezone(timedelta(hours=5, minutes=30), "IST")
        ),
    ],
)
def test_typed_source_values_roundtrip(value):
    result = decode(encode(value, Limits()), Limits())
    assert type(result) is type(value)
    assert result == value
    if isinstance(value, datetime):
        assert result.fold == value.fold
        assert result.tzname() == value.tzname()


@pytest.mark.parametrize(
    "node",
    [
        {"t": "uuid", "v": "not-a-uuid"},
        {"t": "date", "v": "2024-02-31"},
        {"t": "datetime", "v": {"t": "scalar", "v": "2024-01-01"}},
    ],
)
def test_invalid_source_codec_values_rejected(node):
    with pytest.raises(InvalidSnapshot):
        decode(node, Limits())


def test_arbitrary_timezone_callbacks_never_run():
    from datetime import tzinfo

    class Unsafe(tzinfo):
        def utcoffset(self, dt):
            pytest.fail("custom timezone callback executed")

    with pytest.raises(CaptureLimit):
        encode(datetime(2024, 1, 1, tzinfo=Unsafe()), Limits())


async def test_public_sources_property_is_available(recorder):
    assert isinstance(recorder.sources, Sources)

    async def operation():
        return recorder.sources.uuid4()

    await recorder.run(operation)
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_sleep_replay_gives_event_loop_a_turn(recorder, monkeypatch):
    from types import SimpleNamespace

    import rewind.sources as module

    sources = Sources()
    real_sleep = asyncio.sleep

    async def fast_sleep(delay, result=None):
        await real_sleep(0)
        return result

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=fast_sleep))

    async def operation():
        turns = []
        asyncio.get_running_loop().call_soon(turns.append, "scheduled")
        await sources.sleep(600)
        return turns

    assert await recorder.run(operation) == ["scheduled"]
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_changed_sleep_delay_diverges_before_yield(recorder, monkeypatch):
    from types import SimpleNamespace

    import rewind.sources as module

    calls = []

    async def fake_sleep(delay, result=None):
        calls.append(delay)
        return result

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=fake_sleep))
    delay = 600

    async def operation():
        await recorder.sources.sleep(delay)

    await recorder.run(operation)
    delay = 601
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"
    assert calls == [600]
