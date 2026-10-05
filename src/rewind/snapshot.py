"""Validated, immutable portable snapshots."""

import re
from dataclasses import dataclass
from typing import Any

from .codecs import decode, dumps, loads
from .errors import InvalidSnapshot
from .limits import Limits
from .version import SCHEMA_VERSION

ID_PATTERN = re.compile(r"[a-f0-9]{32}\Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidSnapshot(message)


def _validate(data: Any, limits: Limits) -> None:
    _require(type(data) is dict, "snapshot must be an object")
    _require(data.get("schema_version") == SCHEMA_VERSION, "unsupported schema version")
    _require(
        type(data.get("snapshot_id")) is str and bool(ID_PATTERN.fullmatch(data["snapshot_id"])),
        "invalid snapshot ID",
    )
    _require(type(data.get("created_at")) is str, "missing creation time")
    app = data.get("application")
    _require(
        type(app) is dict and set(app) == {"name", "code_digest", "python", "dependencies"},
        "invalid application fingerprint",
    )
    _require(
        all(type(app[k]) is str and app[k] for k in ("name", "code_digest", "python")),
        "invalid application identity",
    )
    _require(
        type(app["dependencies"]) is dict
        and all(type(k) is str and type(v) is str for k, v in app["dependencies"].items()),
        "invalid dependency fingerprint",
    )
    policy = data.get("policy")
    policy_fields = {"capture_values", "capture_bodies", "capture_binary", "exception_args"}
    _require(
        type(policy) is dict
        and set(policy) in (policy_fields, policy_fields | {"redacted_keys"})
        and all(type(policy[k]) is bool for k in policy_fields),
        "unsupported policy",
    )
    if "redacted_keys" in policy:
        keys = policy["redacted_keys"]
        _require(
            type(keys) is list
            and len(keys) <= 64
            and all(type(k) is str and 0 < len(k) <= 128 for k in keys),
            "invalid redaction keys",
        )
    cap = data.get("capture")
    _require(type(cap) is dict and type(cap.get("complete")) is bool, "invalid capture state")
    reasons = cap.get("ineligible_reasons")
    _require(
        type(reasons) is list and len(reasons) <= 32 and all(type(r) is str for r in reasons),
        "invalid eligibility reasons",
    )
    _require(cap["complete"] == (not reasons), "contradictory completeness state")
    inp = data.get("input")
    _require(
        type(inp) is dict and inp.get("kind") in ("callable", "callable_sync", "asgi", "wsgi"),
        "unsupported entry point kind",
    )
    decoded = decode(inp.get("value"), limits)
    if cap["complete"]:
        if inp["kind"] in ("callable", "callable_sync"):
            _require(
                type(decoded) is dict and set(decoded) == {"args", "kwargs"},
                "invalid callable input",
            )
            _require(
                type(decoded["args"]) is tuple and type(decoded["kwargs"]) is dict,
                "invalid callable arguments",
            )
        elif inp["kind"] == "wsgi":
            _require(
                type(decoded) is dict
                and set(decoded) == {"environ"}
                and type(decoded["environ"]) is dict,
                "invalid WSGI input",
            )
        else:
            _require(
                type(decoded) is dict and set(decoded) == {"scope", "body", "chunks"},
                "invalid ASGI input",
            )
            _require(
                type(decoded["scope"]) is dict and type(decoded["body"]) is bytes,
                "invalid ASGI input types",
            )
            chunks = decoded["chunks"]
            _require(
                type(chunks) is list and 0 < len(chunks) <= limits.interactions,
                "invalid ASGI chunks",
            )
            _require(
                all(type(n) is int and n >= 0 for n in chunks)
                and sum(chunks) == len(decoded["body"]),
                "ASGI chunk lengths differ from body",
            )
    interactions = data.get("interactions")
    _require(
        type(interactions) is list and len(interactions) <= limits.interactions,
        "interaction count limit",
    )
    for index, item in enumerate(interactions, 1):
        _require(
            type(item) is dict
            and item.get("sequence") == index
            and type(item.get("sequence")) is int,
            "invalid interaction sequence",
        )
        _require(
            item.get("operation")
            in (
                "http.request",
                "value",
                "db.call",
                "redis.command",
                "redis.pipeline",
                "wsgi.read",
                "messaging.call",
                "cloud.call",
                "filesystem.call",
            ),
            "unknown required operation",
        )
        _require(type(item.get("dependency")) is str, "invalid dependency name")
        if "duration_ns" in item:
            _require(
                type(item["duration_ns"]) is int and item["duration_ns"] >= 0,
                "invalid interaction duration",
            )
        decode(item.get("input"), limits)
        outcome = item.get("outcome")
        if outcome is None:
            _require(not cap["complete"], "unfinished required interaction")
        else:
            _outcome(outcome, limits)
    _outcome(data.get("outcome"), limits)
    if "diagnostics" in data:
        _diagnostics(data["diagnostics"])


def _diagnostics(value: Any) -> None:
    _require(type(value) is dict, "invalid diagnostics")
    # Unknown optional diagnostics can be ignored; required semantics remain strict.
    if value.get("version") != 1:
        return
    _require(set(value) == {"version", "events", "dropped"}, "invalid diagnostics fields")
    _require(type(value["dropped"]) is int and value["dropped"] >= 0, "invalid dropped count")
    events = value["events"]
    _require(type(events) is list and len(events) <= 10000, "invalid diagnostics events")
    previous = 0
    for event in events:
        _require(
            type(event) is dict
            and set(event)
            == {
                "sequence",
                "span_id",
                "parent_id",
                "kind",
                "phase",
                "name",
                "offset_ns",
                "elapsed_ns",
                "exception_type",
            },
            "invalid diagnostic event",
        )
        for key in ("sequence", "span_id", "offset_ns"):
            _require(type(event[key]) is int and event[key] >= 0, "invalid diagnostic counter")
        _require(
            event["sequence"] > previous and event["span_id"] > 0, "invalid diagnostic sequence"
        )
        previous = event["sequence"]
        _require(
            event["parent_id"] is None
            or (type(event["parent_id"]) is int and event["parent_id"] > 0),
            "invalid diagnostic parent",
        )
        _require(event["kind"] in ("function", "span"), "invalid diagnostic kind")
        _require(event["phase"] in ("enter", "return", "exception"), "invalid diagnostic phase")
        _require(
            type(event["name"]) is str
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:\-]{0,127}", event["name"]) is not None,
            "invalid diagnostic name",
        )
        _require(
            event["elapsed_ns"] is None
            or (type(event["elapsed_ns"]) is int and event["elapsed_ns"] >= 0),
            "invalid diagnostic duration",
        )
        _require(
            event["exception_type"] is None
            or (
                type(event["exception_type"]) is str
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:\-]{0,127}", event["exception_type"])
                is not None
            ),
            "invalid diagnostic exception type",
        )


def _outcome(value: Any, limits: Limits) -> None:
    _require(type(value) is dict, "missing outcome")
    if value.get("kind") == "return":
        decode(value.get("value"), limits)
    elif value.get("kind") == "exception":
        _require(type(value.get("type")) is str, "invalid exception type")
        decode(value.get("args"), limits)
    else:
        raise InvalidSnapshot("unknown outcome kind")


@dataclass(frozen=True)
class Snapshot:
    """Serialized bytes are immutable; data returns a detached view."""

    raw: bytes

    @classmethod
    def from_bytes(cls, raw: bytes, limits: Limits | None = None) -> "Snapshot":
        limits = limits or Limits()
        if not isinstance(raw, (bytes, bytearray)):
            raise InvalidSnapshot("artifact must contain JSON bytes")
        if len(raw) > limits.snapshot_bytes:
            raise InvalidSnapshot("artifact exceeds byte limit")
        raw = bytes(raw)
        _validate(loads(raw, limits), limits)
        return cls(raw)

    @classmethod
    def from_dict(cls, data: dict[str, Any], limits: Limits | None = None) -> "Snapshot":
        return cls.from_bytes(dumps(data), limits)

    @property
    def data(self) -> dict[str, Any]:
        import json

        return json.loads(self.raw)

    @property
    def id(self) -> str:
        return str(self.data["snapshot_id"])

    @property
    def complete(self) -> bool:
        return bool(self.data["capture"]["complete"])
