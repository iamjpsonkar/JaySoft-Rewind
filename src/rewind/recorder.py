"""Bounded per-execution recorder. Application exceptions never belong to storage."""

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from .codecs import dumps, encode
from .errors import InvalidSnapshot
from .limits import Limits
from .policy import CapturePolicy
from .snapshot import Snapshot
from .tracing import TraceBuffer, TraceConfig
from .version import SCHEMA_VERSION, __version__


class Recorder:
    def __init__(
        self,
        application: dict[str, Any],
        policy: CapturePolicy,
        limits: Limits,
        kind: str,
        *,
        trace_config: TraceConfig | None = None,
    ) -> None:
        self.application = application
        self.policy = policy
        self.limits = limits
        self.kind = kind
        try:
            self.owner = asyncio.current_task()
        except RuntimeError:
            self.owner = None
        self.owner_thread = threading.get_ident()
        self.started = time.monotonic()
        self.sealed = False
        self.reasons: list[str] = []
        self.interactions: list[dict[str, Any]] = []
        self.interaction_started: dict[int, int] = {}
        self.input = encode(None, limits)
        self.bytes_used = 0
        self.diagnostics = (
            TraceBuffer(trace_config, byte_limit=limits.snapshot_bytes // 8)
            if trace_config is not None and trace_config.enabled
            else None
        )

    def mark(self, reason: str) -> None:
        if not self.sealed and reason not in self.reasons and len(self.reasons) < 32:
            self.reasons.append(reason)

    def check_task(self) -> None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            # ContextVars propagate to asyncio.to_thread, which has no event loop.
            task = None
        if task is not self.owner or threading.get_ident() != self.owner_thread:
            self.mark("child_task_unsupported")

    def pack(self, value: Any) -> dict[str, Any]:
        if self.sealed or "recording_limit" in self.reasons:
            return encode(None, self.limits)
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
        self.interaction_started[slot] = time.perf_counter_ns()
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
        if self.sealed or "recording_limit" in self.reasons:
            return {"kind": "exception", "type": "unavailable", "args": encode(None, self.limits)}
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
            started = self.interaction_started.pop(slot, None)
            if started is not None:
                self.interactions[slot]["duration_ns"] = max(0, time.perf_counter_ns() - started)

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
        if self.diagnostics is not None:
            try:
                remaining = max(0, self.limits.snapshot_bytes - len(dumps(document)) - 32)
                diagnostics = self.diagnostics.finish(byte_budget=remaining)
                if diagnostics is not None:
                    document["diagnostics"] = diagnostics
                    try:
                        return Snapshot.from_dict(document, self.limits)
                    except InvalidSnapshot:
                        # Optional data cannot make an otherwise valid required
                        # recording fail structural/byte validation.
                        document.pop("diagnostics", None)
            except Exception:
                document.pop("diagnostics", None)
        return Snapshot.from_dict(document, self.limits)
