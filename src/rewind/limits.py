"""Finite defaults for capture, serialization, and local storage."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    body_bytes: int = 64 * 1024
    snapshot_bytes: int = 1024 * 1024
    interactions: int = 1000
    depth: int = 32
    items: int = 10_000
    active_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError("limits must be positive integers")
        if self.body_bytes > self.snapshot_bytes:
            raise ValueError("body_bytes cannot exceed snapshot_bytes")
