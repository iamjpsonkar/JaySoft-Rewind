import base64
import json

import pytest

from rewind import CapturePolicy, Limits, Snapshot
from rewind.codecs import decode, loads
from rewind.errors import InvalidSnapshot
from rewind.recorder import Recorder


@pytest.mark.parametrize(
    "body",
    [b'{"x":{"password":"never-save"},"x":0}', b'{"x":1e999}', b'{"x":NaN}'],
)
def test_ambiguous_json_bodies_are_excluded(body):
    reasons = []
    assert CapturePolicy.synthetic().body(body, "application/json", Limits(), reasons.append) == ""
    assert reasons == ["invalid_json_body"]


def test_mixed_case_json_type_is_scrubbed():
    reasons = []
    body = CapturePolicy.synthetic().body(
        b'{"password":"never-save"}', "Application/Problem+JSON", Limits(), reasons.append
    )
    assert json.loads(base64.b64decode(body)) == {"password": "[REDACTED]"}
    assert reasons == ["sensitive_value_removed"]


def test_redaction_cannot_expand_body_past_limit():
    reasons = []
    body = b'{"password":1}'
    result = CapturePolicy.synthetic().body(
        body, "application/json", Limits(body_bytes=len(body)), reasons.append
    )
    assert result == ""
    assert "body_limit" in reasons


def test_json_exponent_overflow_is_rejected():
    with pytest.raises(InvalidSnapshot, match="non-finite"):
        loads(b'{"extra":1e999}', Limits())


async def test_snapshot_detaches_mutable_source(recorder):
    async def operation():
        return 42

    await recorder.run(operation)
    source = bytearray(recorder.store.load(recorder.store.ids()[0]).raw)
    snapshot = Snapshot.from_bytes(source)
    original = snapshot.raw
    source[:] = b"tampered"
    assert type(snapshot.raw) is bytes
    assert snapshot.raw == original
    assert snapshot.complete


async def test_sealed_recorder_does_not_inspect_late_values(recorder):
    active = Recorder(recorder.application, recorder.policy, recorder.limits, "callable")
    active.set_input({"args": (), "kwargs": {}})
    active.seal(active.returned(1))
    count = active.bytes_used
    assert decode(active.pack(object()), active.limits) is None
    active.raised(RuntimeError("late secret"))
    assert active.bytes_used == count
    assert active.reasons == []
