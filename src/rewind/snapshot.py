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
    _require(
        type(policy) is dict
        and set(policy) == {"capture_values", "capture_bodies", "capture_binary", "exception_args"}
        and all(type(v) is bool for v in policy.values()),
        "unsupported policy",
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
        type(inp) is dict and inp.get("kind") in ("callable", "asgi"),
        "unsupported entry point kind",
    )
    decoded = decode(inp.get("value"), limits)
    if cap["complete"]:
        if inp["kind"] == "callable":
            _require(
                type(decoded) is dict and set(decoded) == {"args", "kwargs"},
                "invalid callable input",
            )
            _require(
                type(decoded["args"]) is tuple and type(decoded["kwargs"]) is dict,
                "invalid callable arguments",
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
        _require(item.get("operation") in ("http.request", "value"), "unknown required operation")
        _require(type(item.get("dependency")) is str, "invalid dependency name")
        decode(item.get("input"), limits)
        outcome = item.get("outcome")
        if outcome is None:
            _require(not cap["complete"], "unfinished required interaction")
        else:
            _outcome(outcome, limits)
    _outcome(data.get("outcome"), limits)


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
