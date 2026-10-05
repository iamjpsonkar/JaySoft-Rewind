"""Strict sequential matching with sticky, payload-free divergence reports."""

import asyncio
from dataclasses import asdict, dataclass
from typing import Any, NoReturn

from .codecs import decode, encode
from .errors import ReplayDivergence
from .limits import Limits
from .policy import CapturePolicy
from .snapshot import Snapshot


@dataclass(frozen=True)
class ReplayReport:
    status: str
    detail: str
    consumed: int = 0
    total: int = 0
    isolation: str = "adapter-only"

    @property
    def reproduced(self) -> bool:
        return self.status == "reproduced"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def first_difference(expected: Any, actual: Any, path: str = "input") -> str | None:
    """Never include captured values in errors; even keys may be sensitive."""
    if type(expected) is not type(actual):
        return f"{path}: type differs"
    if isinstance(expected, dict):
        if list(expected) != list(actual):
            return f"{path}: keys or ordering differ"
        for index, key in enumerate(expected):
            result = first_difference(expected[key], actual[key], f"{path}.field[{index}]")
            if result:
                return result
    elif isinstance(expected, (list, tuple)):
        if len(expected) != len(actual):
            return f"{path}: length differs"
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            result = first_difference(left, right, f"{path}[{index}]")
            if result:
                return result
    elif expected != actual:
        return f"{path}: value differs"
    return None


class ReplaySession:
    def __init__(self, snapshot: Snapshot, limits: Limits) -> None:
        self.data = snapshot.data
        self.limits = limits
        self.policy = CapturePolicy(**self.data["policy"])
        self.interactions = self.data["interactions"]
        self.cursor = 0
        self.failure: str | None = None
        self.owner = asyncio.current_task()
        self.closed = False

    def fail(self, detail: str) -> NoReturn:
        self.failure = self.failure or detail
        raise ReplayDivergence(self.failure)

    def consume(self, operation: str, dependency: str, value: Any) -> dict[str, Any]:
        if self.closed:
            self.fail("operation after replay completed")
        if self.failure:
            self.fail(self.failure)
        if asyncio.current_task() is not self.owner:
            self.fail("child task is outside sequential replay scope")
        if self.cursor >= len(self.interactions):
            self.fail(f"unexpected interaction {self.cursor + 1}")
        expected = self.interactions[self.cursor]
        if expected["operation"] != operation or expected["dependency"] != dependency:
            self.fail(f"interaction {self.cursor + 1}: operation or dependency differs")
        packed = encode(value, self.limits)
        difference = first_difference(expected["input"], packed)
        if difference:
            self.fail(f"interaction {self.cursor + 1}: {difference}")
        self.cursor += 1
        return expected["outcome"]

    def report(self, outcome: dict[str, Any]) -> ReplayReport:
        self.closed = True
        if self.failure:
            return ReplayReport("diverged", self.failure, self.cursor, len(self.interactions))
        if self.cursor != len(self.interactions):
            return ReplayReport(
                "diverged", "required interactions left unused", self.cursor, len(self.interactions)
            )
        difference = first_difference(self.data["outcome"], outcome, "outcome")
        return ReplayReport(
            "diverged" if difference else "reproduced",
            difference or "recorded interactions and outcome matched",
            self.cursor,
            len(self.interactions),
        )

    def unpack(self, value: Any) -> Any:
        return decode(value, self.limits)
