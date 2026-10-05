"""Compatibility against actual bytes written by the published 0.1.0a2 package."""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from rewind import CapturePolicy, Limits
from rewind._worker import execute
from rewind.codecs import decode
from rewind.errors import InvalidSnapshot
from rewind.fingerprint import fingerprint
from rewind.snapshot import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "golden-v0.1.json"
SHA256 = "77cd7045f07424d1d1025a773f34679ee32799c2a7f9d7a1c621ecb547c42e70"


def test_published_golden_loads_without_rewriting_bytes():
    raw = GOLDEN.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256
    snapshot = Snapshot.from_bytes(raw)
    assert snapshot.raw == raw
    data = snapshot.data
    assert data["schema_version"] == "0.1"
    assert data["producer"] == {"version": "0.1.0a2"}
    assert snapshot.complete
    assert "diagnostics" not in data
    assert "duration_ns" not in data["interactions"][0]
    assert Snapshot.from_dict(data).raw == raw
    assert data["application"]["code_digest"] == fingerprint(
        "golden-contract", [FIXTURES / "golden_app.py"]
    )["code_digest"]
    data["producer"]["version"] = "detached mutation"
    assert snapshot.raw == raw
    assert snapshot.data["producer"]["version"] == "0.1.0a2"


def test_published_typed_input_and_observation_decode_exactly():
    data = Snapshot.from_bytes(GOLDEN.read_bytes()).data
    limits = Limits()
    incoming = decode(data["input"]["value"], limits)
    assert incoming == {"args": ({"fixture": b"input", "attempts": (1, 2)},), "kwargs": {}}
    event = data["interactions"][0]
    assert event["sequence"] == 1
    assert event["operation"] == "value"
    assert event["dependency"] == "fixture.value"
    assert decode(event["input"], limits) is None
    observed = decode(event["outcome"]["value"], limits)
    assert observed == {
        "day": date(2024, 1, 2),
        "moment": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        "identifier": UUID("12345678-1234-5678-1234-567812345678"),
        "binary": b"synthetic fixture",
        "tuple": (7, None, True),
    }
    assert decode(data["outcome"]["value"], limits) == {
        "input": incoming["args"][0], "observed": observed,
    }


def test_four_field_policy_and_custom_policy_compatibility():
    data = Snapshot.from_bytes(GOLDEN.read_bytes()).data
    assert set(data["policy"]) == {
        "capture_values", "capture_bodies", "capture_binary", "exception_args",
    }
    assert CapturePolicy(**data["policy"]).to_dict() == data["policy"]
    data["policy"]["redacted_keys"] = ["customer_id"]
    extended = Snapshot.from_dict(data)
    assert CapturePolicy(**extended.data["policy"]).redacted_keys == ("customer_id",)


@pytest.mark.parametrize("mutation", ["schema", "operation", "input_codec", "result_codec"])
def test_unknown_required_semantics_rejected_before_factory_import(tmp_path, monkeypatch, mutation):
    data = Snapshot.from_bytes(GOLDEN.read_bytes()).data
    if mutation == "schema":
        data["schema_version"] = "unknown-required-schema"
    elif mutation == "operation":
        data["interactions"][0]["operation"] = "unknown.required.operation"
    elif mutation == "input_codec":
        data["input"]["value"] = {"t": "unknown-required-codec", "v": None}
    else:
        data["outcome"]["value"] = {"t": "unknown-required-codec", "v": None}
    raw = json.dumps(data).encode()
    with pytest.raises(InvalidSnapshot):
        Snapshot.from_bytes(raw)
    artifact = tmp_path / "invalid.json"
    artifact.write_bytes(raw)
    imports = []

    def forbidden_import(name):
        imports.append(name)
        raise AssertionError("application factory imported before artifact validation")

    monkeypatch.setattr("rewind._worker.importlib.import_module", forbidden_import)
    report = execute(str(artifact), "never_imported:factory", "adapter-only")
    assert report.status == "replay_error"
    assert imports == []


def test_unknown_optional_diagnostic_version_does_not_change_required_data():
    data = Snapshot.from_bytes(GOLDEN.read_bytes()).data
    required = data["interactions"]
    data["diagnostics"] = {"version": "future-optional", "summary": "synthetic"}
    extended = Snapshot.from_dict(data)
    assert extended.complete
    assert extended.data["interactions"] == required
    assert extended.data["diagnostics"] == data["diagnostics"]
