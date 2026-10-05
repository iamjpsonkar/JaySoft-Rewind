import json

import pytest

from rewind import Limits, Snapshot, TraceConfig, span, trace
from rewind.cli import main
from rewind.errors import InvalidSnapshot


def saved(recorder):
    return recorder.store.load(recorder.store.ids()[0])


async def test_function_timeline_survives_persistence_and_is_optional_on_replay(recorder, capsys):
    recorder.trace_config = TraceConfig(enabled=True)

    @trace(name="calculate")
    def calculate():
        with span("read_clock"):
            return recorder.sources.time()

    @recorder.trace(name="entry")
    async def operation():
        return calculate()

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete
    events = snapshot.data["diagnostics"]["events"]
    assert [event["phase"] for event in events] == ["enter"] * 3 + ["return"] * 3
    assert events[1]["parent_id"] == events[0]["span_id"]
    assert (await recorder.replay(snapshot, operation)).reproduced
    path = recorder.store.path / f"{snapshot.id}.rewind.json"
    assert main(["inspect", str(path), "--timeline"]) == 0
    assert json.loads(capsys.readouterr().out)["diagnostics"]["events"] == events


async def test_diagnostic_overflow_preserves_all_required_observations(recorder):
    recorder.trace_config = TraceConfig(enabled=True, max_events=2)

    @trace(name="value")
    def observation():
        return recorder.sources.uuid4()

    async def operation():
        return [observation() for _ in range(8)]

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete
    assert len(snapshot.data["interactions"]) == 8
    assert len(snapshot.data["diagnostics"]["events"]) == 2
    assert snapshot.data["diagnostics"]["dropped"] == 14
    assert (await recorder.replay(snapshot, operation)).reproduced


async def test_small_artifact_budget_does_not_drop_required_data_for_tracing(recorder):
    recorder.trace_config = TraceConfig(enabled=True, max_events=1000)
    recorder.limits = Limits(body_bytes=128, snapshot_bytes=4096)

    @trace(name="noop")
    def noop():
        return None

    async def operation():
        for _ in range(100):
            noop()
        return recorder.sources.time()

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert len(snapshot.raw) <= 4096
    assert snapshot.complete
    assert (await recorder.replay(snapshot, operation)).reproduced


async def test_corrupt_known_diagnostics_rejected_but_future_optional_version_ignored(recorder):
    async def operation():
        return None

    await recorder.run(operation)
    data = saved(recorder).data
    data["diagnostics"] = {"version": 1, "events": [], "dropped": -1}
    with pytest.raises(InvalidSnapshot):
        Snapshot.from_dict(data)
    data["diagnostics"] = {"version": 999, "future": "optional"}
    assert Snapshot.from_dict(data).complete


async def test_discard_releases_trace_buffer(recorder):
    from rewind import Retention, context

    recorder.trace_config = TraceConfig(enabled=True)
    recorder.retain = Retention(exceptions=False, status_at_least=None)
    retained = []

    @trace(name="discarded")
    async def operation():
        retained.append(context.current.get())

    await recorder.run(operation)
    assert retained[0].diagnostics.finish() is None
    assert recorder.store.ids() == []
