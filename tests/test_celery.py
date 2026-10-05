"""Actual Celery eager tasks and an embedded worker using the memory broker."""

import asyncio
from dataclasses import replace

import celery
import pytest
from celery.contrib.testing.worker import start_worker

from rewind import CapturePolicy, context
from rewind.adapters.celery import RecordingCelery


class UnsupportedTaskError(Exception):
    pass


def saved(recorder):
    ids = recorder.store.ids()
    assert len(ids) == 1
    return recorder.store.load(ids[0])


@pytest.fixture
def application():
    app = celery.Celery("rewind-fixture", broker="memory://", backend="cache+memory://")
    app.conf.update(task_always_eager=True, task_serializer="json", accept_content=["json"],
                    result_serializer="json", worker_hijack_root_logger=False)
    yield app
    app.close()


async def test_real_eager_task_and_result_observations(recorder, application):
    calls = []

    @application.task(shared=False, name="fixture.add")
    def add(a, b):
        calls.append((a, b))
        assert context.current.get() is None
        return a + b

    client = RecordingCelery(application)

    async def operation():
        result = client.task("fixture.add").delay(3, 4)
        identifier = result.id
        assert result.state == "SUCCESS" and result.status == "SUCCESS"
        assert result.ready() and result.successful() and not result.failed()
        assert result.result == 7
        value = result.get(timeout=1)
        result.forget()
        return identifier, value

    original = await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete, snapshot.data["capture"]
    client.inner = None
    report = await recorder.replay(snapshot, operation)
    assert report.reproduced, report
    assert calls == [(3, 4)]
    assert original[1] == 7


@pytest.mark.parametrize("propagate", [True, False])
async def test_task_exception_as_raised_or_returned_value(recorder, application, propagate):
    @application.task(shared=False, name="fixture.fail")
    def fail():
        raise ValueError("synthetic failure")

    client = RecordingCelery(application)

    async def operation():
        result = client.task("fixture.fail").apply_async()
        assert result.status == "FAILURE" and result.failed()
        assert type(result.result) is ValueError
        if propagate:
            try:
                result.get(propagate=True)
            except ValueError as exc:
                return exc.args
        else:
            error = result.get(propagate=False)
            assert type(error) is ValueError
            return error.args

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete
    client.inner = None
    assert (await recorder.replay(snapshot, operation)).reproduced


@pytest.mark.parametrize("change", ["name", "args", "kwargs", "options", "get", "dependency"])
async def test_changed_celery_observations_diverge(recorder, application, change):
    @application.task(shared=False, name="fixture.identity")
    def identity(value, **kwargs):
        return value

    client = RecordingCelery(application)
    changed = False

    async def operation():
        name = "other" if changed and change == "name" else "fixture.identity"
        return client.task(name).apply_async(
            args=(2 if changed and change == "args" else 1,),
            kwargs={"x": 2 if changed and change == "kwargs" else 1},
            headers={"trace": "other" if changed and change == "options" else "fixture"},
        ).get(timeout=2 if changed and change == "get" else 1)

    assert await recorder.run(operation) == 1
    changed = True
    if change == "dependency":
        client.dependency = "other"
    client.inner = None
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"


@pytest.mark.parametrize("policy", [
    CapturePolicy(), replace(CapturePolicy.synthetic(), redacted_keys=("customer_id",))
])
async def test_celery_submission_privacy(recorder, application, policy):
    recorder.policy = policy

    @application.task(shared=False, name="fixture.accept")
    def accept(**kwargs):
        assert kwargs["customer_id"] == "PRIVATE_MESSAGE"
        return None

    client = RecordingCelery(application)

    async def operation():
        client.task("fixture.accept").apply_async(
            kwargs={"customer_id": "PRIVATE_MESSAGE"}, headers={"Authorization": "PRIVATE_HEADER"},
        ).get()

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"PRIVATE_MESSAGE" not in snapshot.raw and b"PRIVATE_HEADER" not in snapshot.raw


async def test_result_handle_owner_checked_even_for_id(recorder, application):
    @application.task(shared=False, name="fixture.owner_identity")
    def identity():
        return 1

    client = RecordingCelery(application)
    result = client.task("fixture.owner_identity").delay()

    async def operation():
        assert type(result.id) is str
        return None

    await recorder.run(operation)
    assert not saved(recorder).complete


async def test_child_task_result_access_ineligible(recorder, application):
    @application.task(shared=False, name="fixture.child_identity")
    def identity():
        return 1

    client = RecordingCelery(application)

    async def operation():
        result = client.task("fixture.child_identity").delay()

        async def child():
            return result.id, result.get()

        await asyncio.create_task(child())

    await recorder.run(operation)
    assert "child_task_unsupported" in saved(recorder).data["capture"]["ineligible_reasons"]


async def test_send_task_real_memory_broker_worker(recorder, application):
    application.conf.task_always_eager = False
    calls = []

    @application.task(shared=False, name="fixture.worker_add")
    def add(a, b):
        calls.append((a, b))
        return a + b

    client = RecordingCelery(application)

    async def operation():
        result = client.send_task("fixture.worker_add", args=(8, 9))
        value = result.get(timeout=5)
        state = result.status
        result.forget()
        return result.id, state, value

    with start_worker(application, perform_ping_check=False, pool="solo", loglevel="CRITICAL"):
        original = await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete, snapshot.data["capture"]
    client.inner = None
    report = await recorder.replay(snapshot, operation)
    assert report.reproduced, report
    assert original[1:] == ("SUCCESS", 17)
    assert calls == [(8, 9)]


async def test_copied_thread_result_access_marks_capture_ineligible(recorder, application):
    @application.task(shared=False, name="fixture.thread_identity")
    def identity():
        return 1

    client = RecordingCelery(application)

    async def operation():
        result = client.task("fixture.thread_identity").delay()
        return await asyncio.to_thread(lambda: result.id)

    assert type(await recorder.run(operation)) is str
    assert not saved(recorder).complete


async def test_unsupported_task_exception_is_not_serialized(recorder, application):
    @application.task(shared=False, name="fixture.unsupported_failure")
    def fail():
        raise UnsupportedTaskError("PRIVATE_EXCEPTION")

    client = RecordingCelery(application)

    async def operation():
        result = client.task("fixture.unsupported_failure").delay()
        value = result.get(propagate=False)
        assert type(value) is UnsupportedTaskError and value.args == ("PRIVATE_EXCEPTION",)

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"PRIVATE_EXCEPTION" not in snapshot.raw
