import pytest

from rewind import Sources


def saved(recorder):
    return recorder.store.load(recorder.store.ids()[0])


async def test_environment_is_replayed_without_live_access(recorder, monkeypatch):
    sources = Sources()
    monkeypatch.setenv("REWIND_REGION", "fixture-region")

    async def operation():
        return [
            sources.getenv("REWIND_REGION"),
            sources.getenv("REWIND_ABSENT", "fallback"),
            sources.environ("REWIND_REGION"),
        ]

    assert await recorder.run(operation) == ["fixture-region", "fallback", "fixture-region"]
    monkeypatch.setenv("REWIND_REGION", "different")
    monkeypatch.setenv("REWIND_ABSENT", "different")
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_missing_required_environment_exception_replayed(recorder, monkeypatch):
    monkeypatch.delenv("REWIND_ABSENT", raising=False)
    sources = Sources()

    async def operation():
        return sources.environ("REWIND_ABSENT")

    with pytest.raises(KeyError):
        await recorder.run(operation)
    monkeypatch.setenv("REWIND_ABSENT", "now-present")
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_secret_environment_observation_excluded(recorder, monkeypatch):
    monkeypatch.setenv("PAYMENT_API_KEY", "fixture-private-value")
    sources = Sources()

    async def operation():
        return len(sources.getenv("PAYMENT_API_KEY"))

    assert await recorder.run(operation) == len("fixture-private-value")
    artifact = saved(recorder)
    assert b"fixture-private-value" not in artifact.raw
    assert "sensitive_environment_removed" in artifact.data["capture"]["ineligible_reasons"]
    assert (await recorder.replay(artifact, operation)).status == "ineligible"


async def test_sync_wait_and_additional_random_observations_skip_live_sources(
    recorder, monkeypatch
):
    from types import SimpleNamespace

    import rewind.sources as module

    sources = Sources()

    async def operation():
        sources.sleep_sync(0)
        return sources.getrandbits(16), sources.randrange(1, 100, 2)

    await recorder.run(operation)

    def forbidden(*args):
        pytest.fail("live source accessed during replay")

    monkeypatch.setattr(module, "_time", SimpleNamespace(sleep=forbidden))
    monkeypatch.setattr(
        module, "_random", SimpleNamespace(getrandbits=forbidden, randrange=forbidden)
    )
    assert (await recorder.replay(saved(recorder), operation)).reproduced
