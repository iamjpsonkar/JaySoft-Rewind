"""Typed retention conditions; no evaluated expression strings."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Retention:
    exceptions: bool = True
    status_at_least: int | None = 500
    duration_at_least: float | None = None
    always: bool = False

    def __post_init__(self) -> None:
        if self.status_at_least is not None and not 100 <= self.status_at_least <= 599:
            raise ValueError("status threshold must be an HTTP status")
        if self.duration_at_least is not None and self.duration_at_least < 0:
            raise ValueError("duration threshold cannot be negative")

    def matches(self, *, failed: bool, status: int | None, duration: float) -> bool:
        return (
            self.always
            or (self.exceptions and failed)
            or (
                self.status_at_least is not None
                and status is not None
                and status >= self.status_at_least
            )
            or (self.duration_at_least is not None and duration >= self.duration_at_least)
        )
