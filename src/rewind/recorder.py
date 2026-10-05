"""Bounded per-execution recorder. Application exceptions never belong to storage."""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from .codecs import dumps, encode
from .limits import Limits
from .policy import CapturePolicy
from .snapshot import Snapshot
from .version import SCHEMA_VERSION, __version__


class Recorder:
    def __init__(
        self, application: dict[str, Any], policy: CapturePolicy, limits: Limits, kind: str
    ) -> None:
        self.application = application
        self.policy = policy
        self.limits = limits
        self.kind = kind
        self.owner = asyncio.current_task()
        self.started = time.monotonic()
        self.sealed = False
        self.reasons: list[str] = []
        self.interactions: list[dict[str, Any]] = []
        self.input = encode(None, limits)
        self.bytes_used = 0

    def mark(self, reason: str) -> None:
        if not self.sealed and reason not in self.reasons and len(self.reasons) < 32:
            self.reasons.append(reason)

    def check_task(self) -> None:
        if asyncio.current_task() is not self.owner:
            self.mark("child_task_unsupported")

    def pack(self, value: Any) -> dict[str, Any]:
        try:
            packed = self.policy.value(value, self.limits, self.mark)
            self.bytes_used += len(dumps(packed))
            if self.bytes_used > self.limits.snapshot_bytes // 2:
                self.mark("recording_limit")
                return encode(None, self.limits)
            return packed
        except Exception:
            self.mark("value_capture_failed")
            return encode(None, self.limits)

    def set_input(self, value: Any) -> None:
        if not self.sealed:
            self.input = self.pack(value)

    def begin(self, operation: str, dependency: str, value: Any) -> int | None:
        if self.sealed:
            return None
        self.check_task()
        if len(self.interactions) >= self.limits.interactions:
            self.mark("interaction_limit")
            return None
        if any(item["outcome"] is None for item in self.interactions):
            self.mark("overlapping_interactions")
        if "recording_limit" in self.reasons:
            return None
        slot = len(self.interactions)
        self.interactions.append(
            {
                "sequence": slot + 1,
                "operation": operation,
                "dependency": dependency,
                "input": self.pack(value),
                "outcome": None,
            }
        )
        return slot

    def returned(self, value: Any) -> dict[str, Any]:
        return {"kind": "return", "value": self.pack(value)}

    def raised(self, exc: BaseException) -> dict[str, Any]:
        try:
            outcome = self.policy.exception(exc, self.limits, self.mark)
            self.bytes_used += len(dumps(outcome))
            if self.bytes_used > self.limits.snapshot_bytes // 2:
                self.mark("recording_limit")
                outcome["args"] = encode(None, self.limits)
            return outcome
        except Exception:
            self.mark("exception_capture_failed")
            return {"kind": "exception", "type": "unavailable", "args": encode(None, self.limits)}

    def finish(self, slot: int | None, outcome: dict[str, Any]) -> None:
        if slot is not None and not self.sealed:
            self.check_task()
            self.interactions[slot]["outcome"] = outcome

    def seal(self, outcome: dict[str, Any]) -> Snapshot:
        if any(item["outcome"] is None for item in self.interactions):
            self.mark("unfinished_interaction")
        document = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "producer": {"version": __version__},
            "application": self.application,
            "policy": self.policy.to_dict(),
            "capture": {"complete": not self.reasons, "ineligible_reasons": self.reasons.copy()},
            "input": {"kind": self.kind, "value": self.input},
            "interactions": self.interactions,
            "outcome": outcome,
        }
        self.sealed = True
        return Snapshot.from_dict(document, self.limits)
