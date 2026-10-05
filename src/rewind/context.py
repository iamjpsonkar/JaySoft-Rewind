"""Execution-local state shared by adapters and explicit value providers."""

from contextvars import ContextVar
from typing import Any

current: ContextVar[Any] = ContextVar("rewind_execution", default=None)
