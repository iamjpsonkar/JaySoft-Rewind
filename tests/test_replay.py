import asyncio
import json

import httpx
import pytest

from rewind import CapturePolicy, Limits, Retention, Rewind
from rewind.codecs import decode
from rewind.context import current


def saved(recorder):
    ids = recorder.store.ids()
    assert len(ids) == 1
    return recorder.store.load(ids[0])


async def test_missing_field_failure_replays_without_calling_live_transport(recorder):
    calls = 0

    async def gateway(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    async def payment(order_id):
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(gateway))
        ) as client:
            response = await client.get(f"https://gateway.example/orders/{order_id}")
            return response.json()["payment_id"]

    with pytest.raises(KeyError):
        await recorder.run(payment, "demo")
    snapshot = saved(recorder)
    assert snapshot.complete
    report = await recorder.replay(snapshot, payment)
    assert report.reproduced, report
    assert report.consumed == 1
    assert calls == 1
    assert current.get() is None


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
async def test_http_exceptions_replay_in_same_client_class(recorder, failure):
    def gateway(request):
        raise failure("synthetic timeout", request=request)

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(gateway))
        ) as client:
            try:
                await client.get("https://gateway.example/")
            except failure as exc:
                return [type(exc).__name__, str(exc), str(exc.request.url)]

    await recorder.run(operation)
    report = await recorder.replay(saved(recorder), operation)
    assert report.reproduced, report


async def test_repeated_requests_preserve_distinct_results(recorder):
    count = 0

    def gateway(request):
        nonlocal count
        count += 1
        return httpx.Response(503 if count == 1 else 200, json={"attempt": count})

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(gateway))
        ) as client:
            a = await client.get("https://gateway.example/")
            b = await client.get("https://gateway.example/")
            return [a.status_code, a.json(), b.status_code, b.json()]

    await recorder.run(operation)
    assert (await recorder.replay(saved(recorder), operation)).reproduced
    assert count == 2


async def test_changed_http_input_reports_first_difference_even_when_caught(recorder):
    url = "https://gateway.example/original"

    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(lambda r: httpx.Response(200)))
        ) as client:
            try:
                await client.get(url)
            except Exception:
                pass

    await recorder.run(operation)
    url = "https://gateway.example/changed-secret-value"
    report = await recorder.replay(saved(recorder), operation)
    assert report.status == "diverged"
    assert "interaction 1" in report.detail
    assert "changed-secret-value" not in report.detail


@pytest.mark.parametrize("replay_count", [0, 2])
async def test_missing_and_extra_interactions_diverge(recorder, replay_count):
    count = 1

    async def operation():
        for _ in range(count):
            recorder.value("id", lambda: "id-1")

    await recorder.run(operation)
    count = replay_count
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"


async def test_changed_outcome_diverges(recorder):
    answer = "old"

    async def operation():
        return answer

    await recorder.run(operation)
    answer = "new"
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"


async def test_nondeterministic_provider_values_are_replayed(recorder):
    count = 0

    def next_id():
        nonlocal count
        count += 1
        return count

    async def operation():
        return [recorder.value("counter", next_id), recorder.value("counter", next_id)]

    assert await recorder.run(operation) == [1, 2]
    assert (await recorder.replay(saved(recorder), operation)).reproduced
    assert count == 2


async def test_capture_preserves_input_before_mutation(recorder):
    async def operation(value):
        value.append("mutated")
        return value

    await recorder.run(operation, ["before"])
    snapshot = saved(recorder)
    assert decode(snapshot.data["input"]["value"], recorder.limits)["args"] == (["before"],)
    assert (await recorder.replay(snapshot, operation)).reproduced


async def test_parallel_root_requests_are_isolated(recorder):
    async def operation(number):
        await asyncio.sleep(0)
        return recorder.value("id", lambda: number)

    assert await asyncio.gather(*(recorder.run(operation, i) for i in range(20))) == list(range(20))
    snapshots = [recorder.store.load(i) for i in recorder.store.ids()]
    assert len(snapshots) == 20
    for snap in snapshots:
        assert snap.complete
        assert (await recorder.replay(snap, operation)).reproduced


async def test_child_task_interactions_make_capture_ineligible(recorder):
    async def child():
        return recorder.value("id", lambda: 1)

    async def operation():
        return await asyncio.create_task(child())

    await recorder.run(operation)
    report = await recorder.replay(saved(recorder), operation)
    assert report.status == "ineligible"
    assert "child_task" in report.detail


async def test_cancellation_is_preserved_and_context_reset(recorder):
    async def operation():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await recorder.run(operation)
    assert not saved(recorder).complete
    assert current.get() is None


async def test_storage_failure_cannot_replace_application_outcome(recorder, monkeypatch):
    def broken_save(snapshot):
        raise OSError("private error text")

    monkeypatch.setattr(recorder.store, "save", broken_save)

    async def operation():
        return "original"

    assert await recorder.run(operation) == "original"
    assert recorder.metrics["persistence_failed"] == 1
    assert recorder._active == 0


async def test_disabled_and_condition_discard_do_not_save(recorder):
    async def operation():
        return 42

    recorder.enabled = False
    assert await recorder.run(operation) == 42
    recorder.enabled = True
    recorder.retain = Retention()
    assert await recorder.run(operation) == 42
    assert recorder.store.ids() == []


async def test_secret_header_removed_and_replay_rejected(recorder):
    async def operation():
        async with httpx.AsyncClient(
            transport=recorder.httpx_transport(httpx.MockTransport(lambda r: httpx.Response(200)))
        ) as client:
            await client.get("https://gateway.example/", headers={"Authorization": "do-not-save"})

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert b"do-not-save" not in snapshot.raw
    assert (await recorder.replay(snapshot, operation)).status == "ineligible"


async def test_default_policy_never_persists_arguments(recorder):
    recorder.policy = CapturePolicy()

    async def operation(secret):
        return secret

    await recorder.run(operation, "sensitive")
    snapshot = saved(recorder)
    assert b"sensitive" not in snapshot.raw
    assert not snapshot.complete


async def test_interaction_limit_preserves_application_behavior(recorder):
    recorder.limits = Limits(interactions=1)

    async def operation():
        return [recorder.value("id", lambda: 1), recorder.value("id", lambda: 2)]

    assert await recorder.run(operation) == [1, 2]
    assert not saved(recorder).complete


async def test_modified_source_fails_compatibility(recorder, tmp_path):
    source = tmp_path / "application.py"
    source.write_text("VERSION = 1\n")
    original = Rewind(
        application="demo",
        code_paths=[source],
        store=recorder.store,
        policy=CapturePolicy.synthetic(),
        retain=Retention(always=True),
    )

    async def operation():
        return 1

    await original.run(operation)
    source.write_text("VERSION = 2\n")
    changed = Rewind(application="demo", code_paths=[source])
    assert (await changed.replay(saved(recorder), operation)).status == "incompatible"


async def test_decorator_preserves_name_and_nested_capture_joins(recorder):
    @recorder.capture()
    async def inner():
        return recorder.value("id", lambda: 42)

    @recorder.capture()
    async def outer():
        return await inner()

    assert outer.__name__ == "outer"
    assert await outer() == 42
    assert (await recorder.replay(saved(recorder), outer)).reproduced


async def test_loading_snapshot_rejects_tampered_schema(recorder):
    from rewind import Snapshot
    from rewind.errors import InvalidSnapshot

    async def operation():
        return 1

    await recorder.run(operation)
    data = saved(recorder).data
    data["schema_version"] = "999"
    with pytest.raises(InvalidSnapshot):
        Snapshot.from_bytes(json.dumps(data).encode())
