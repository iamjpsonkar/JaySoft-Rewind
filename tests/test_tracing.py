import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rewind import context
from rewind.codecs import dumps
from rewind.tracing import TraceBuffer, TraceConfig, span, trace


@pytest.fixture
def diagnostics():
    buffer = TraceBuffer(TraceConfig(enabled=True))
    token = context.current.set(SimpleNamespace(diagnostics=buffer, sealed=False))
    try:
        yield buffer
    finally:
        context.current.reset(token)
        buffer.clear()


def test_sync_function_nested_span_and_no_values(diagnostics):
    @trace(name="billing.calculate")
    def calculate(password):
        with span("lookup"):
            return {"password": password}

    assert calculate("PRIVATE_TOKEN") == {"password": "PRIVATE_TOKEN"}
    data = diagnostics.finish()
    events = data["events"]
    assert [event["phase"] for event in events] == ["enter", "enter", "return", "return"]
    assert events[1]["parent_id"] == events[0]["span_id"]
    assert events[0]["parent_id"] is None
    assert events[2]["elapsed_ns"] >= 0
    assert events[0]["kind"] == "function"
    assert events[1]["kind"] == "span"
    assert "PRIVATE_TOKEN" not in repr(data)
    assert data["dropped"] == 0
    assert calculate.__name__ == "calculate"


async def test_async_functions_and_spans(diagnostics):
    @trace
    async def helper():
        async with span("waiting"):
            await asyncio.sleep(0)
        return 23

    assert await helper() == 23
    assert [event["phase"] for event in diagnostics.finish()["events"]] == [
        "enter", "enter", "return", "return",
    ]


def test_exception_identity_and_no_message_or_arguments(diagnostics):
    error = ValueError("PRIVATE_EXCEPTION")

    @trace
    def failing():
        raise error

    with pytest.raises(ValueError) as raised:
        failing()
    assert raised.value is error
    data = diagnostics.finish()
    event = data["events"][-1]
    assert event["phase"] == "exception"
    assert event["exception_type"] == "ValueError"
    assert "PRIVATE_EXCEPTION" not in repr(data)


async def test_cancellation_does_not_change_control_flow(diagnostics):
    @trace
    async def cancelled():
        raise asyncio.CancelledError("PRIVATE_CANCEL")

    with pytest.raises(asyncio.CancelledError):
        await cancelled()
    data = diagnostics.finish()
    assert data["events"][-1]["exception_type"] == "CancelledError"
    assert "PRIVATE_CANCEL" not in repr(data)


@pytest.mark.parametrize("config", [
    TraceConfig(), TraceConfig(enabled=True, max_events=2),
    TraceConfig(enabled=True, max_bytes=1), TraceConfig(enabled=True, max_depth=1),
])
def test_disabled_and_bounded_diagnostics(config):
    buffer = TraceBuffer(config)
    token = context.current.set(SimpleNamespace(diagnostics=buffer, sealed=False))

    @trace(name="recursive")
    def recursive(n):
        return recursive(n - 1) + 1 if n else 0

    try:
        assert recursive(3) == 3
    finally:
        context.current.reset(token)
    data = buffer.finish()
    assert len(data["events"]) <= config.max_events
    if config.enabled:
        assert len(data["events"]) + data["dropped"] == 8
    else:
        assert data == {"version": 1, "events": [], "dropped": 0}


def test_byte_budget_never_exceeded(diagnostics):
    for _ in range(20):
        with span("work"):
            pass
    data = diagnostics.finish(byte_budget=300)
    assert len(dumps(data)) <= 300
    assert data["dropped"] > 0
    assert diagnostics.finish() is None


def test_summary_may_be_omitted_if_no_space(diagnostics):
    with span("work"):
        pass
    assert diagnostics.finish(byte_budget=1) is None


def test_sealed_recorder_and_closed_buffer_pass_through(diagnostics):
    active = context.current.get()
    active.sealed = True
    with span("sealed"):
        pass
    assert diagnostics.finish()["events"] == []
    active.sealed = False
    with span("closed"):
        pass
    assert diagnostics.finish() is None


def test_generator_functions_rejected_explicitly():
    def generator():
        yield 1

    async def async_generator():
        yield 1

    with pytest.raises(TypeError, match="generator"):
        trace(generator)
    with pytest.raises(TypeError, match="generator"):
        trace(async_generator)


@pytest.mark.parametrize("name", ["", "dynamic secret value", "x" * 129, "a\nb", 12])
def test_invalid_names_rejected_before_capture(name):
    with pytest.raises(ValueError):
        span(name)
    with pytest.raises(ValueError):
        trace(lambda: None, name=name)


@pytest.mark.parametrize("kwargs", [
    {"enabled": 1}, {"max_events": 0}, {"max_bytes": True}, {"max_depth": -1},
    {"max_events": 10001}, {"max_bytes": 1024 * 1024 + 1}, {"max_depth": 129},
])
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        TraceConfig(**kwargs)


def test_copied_thread_contexts_have_ordered_bounded_events(diagnostics):
    @trace(name="thread.worker")
    def worker():
        return 1

    with span("parent"):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(contextvars.copy_context().run, worker) for _ in range(20)]
            assert sum(future.result() for future in futures) == 20
    data = diagnostics.finish()
    events = data["events"]
    assert len(events) == 42
    assert [event["sequence"] for event in events] == list(range(1, 43))
    parent = events[0]["span_id"]
    assert all(event["parent_id"] == parent for event in events[1:-1])


async def test_async_child_context_inherits_diagnostic_parent(diagnostics):
    @trace(name="child")
    async def worker():
        await asyncio.sleep(0)
        return 1

    with span("parent"):
        assert sum(await asyncio.gather(worker(), worker())) == 2
    events = diagnostics.finish()["events"]
    assert len(events) == 6
    assert all(event["parent_id"] == events[0]["span_id"] for event in events[1:-1])


def test_optional_failures_do_not_mask_results_or_exceptions(diagnostics, monkeypatch):
    def failed(**kwargs):
        raise RuntimeError("diagnostic failure")

    monkeypatch.setattr(diagnostics, "emit", failed)

    @trace(name="returns")
    def returns():
        return 9

    error = ValueError("application failure")

    @trace(name="raises")
    def raises():
        raise error

    assert returns() == 9
    with pytest.raises(ValueError) as raised:
        raises()
    assert raised.value is error
    assert diagnostics.finish()["dropped"] == 4


def test_diagnostics_do_not_touch_required_interactions(diagnostics):
    active = context.current.get()
    required = [{"sequence": 1, "payload": "required"}]
    active.interactions = required
    active.reasons = []
    for _ in range(300):
        with span("event"):
            pass
    assert active.interactions is required
    assert active.reasons == []
    data = diagnostics.finish()
    assert data["dropped"] > 0


def test_trace_outside_capture_and_replay_pass_through():
    @trace(name="identity")
    def identity(value):
        return value

    marker = object()
    assert identity(marker) is marker
    token = context.current.set(SimpleNamespace(closed=False))
    try:
        assert identity(marker) is marker
    finally:
        context.current.reset(token)


def test_span_state_restored_after_exception(diagnostics):
    with pytest.raises(ValueError):
        with span("fails"):
            raise ValueError
    with span("next"):
        pass
    data = diagnostics.finish()
    assert data["events"][2]["parent_id"] is None


def test_different_capture_does_not_inherit_parent(diagnostics):
    nested = TraceBuffer(TraceConfig(enabled=True))
    with span("outer"):
        token = context.current.set(SimpleNamespace(diagnostics=nested, sealed=False))
        try:
            with span("inner"):
                pass
        finally:
            context.current.reset(token)
    assert nested.finish()["events"][0]["parent_id"] is None


def test_ring_keeps_latest_events_and_reports_every_eviction():
    buffer = TraceBuffer(TraceConfig(enabled=True, max_events=2))
    token = context.current.set(SimpleNamespace(diagnostics=buffer, sealed=False))
    try:
        for _ in range(3):
            with span("last"):
                pass
    finally:
        context.current.reset(token)
    data = buffer.finish()
    assert [event["sequence"] for event in data["events"]] == [5, 6]
    assert data["dropped"] == 4


def test_inherited_buffer_never_takes_parent_process_lock(diagnostics, monkeypatch):
    diagnostics._lock.acquire()
    try:
        monkeypatch.setattr("rewind.tracing.os.getpid", lambda: diagnostics._pid + 1)
        with span("forked"):
            pass
        diagnostics.drop()
        diagnostics.clear()
        assert diagnostics.finish() is None
    finally:
        diagnostics._lock.release()
