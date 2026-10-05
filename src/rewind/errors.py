"""Errors raised by the tool, distinct from captured application failures."""


class RewindError(Exception):
    """Base class for errors with safe, payload-free messages."""


class InvalidSnapshot(RewindError):
    """An artifact violates its format or load limits."""


class CaptureLimit(RewindError):
    """A value cannot be retained within the configured capture boundary."""


class ReplayDivergence(RewindError):
    """An operation differs from the next recorded interaction."""
