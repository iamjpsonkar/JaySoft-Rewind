"""Fresh-process replay and a Python-level network guard for trusted local code."""

import json
import math
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import Rewind
from .errors import RewindError
from .replay import ReplayReport


@dataclass(frozen=True)
class ReplayTarget:
    """Returned by the developer-selected factory; never selected by artifact content."""

    rewind: Rewind
    entrypoint: Callable[..., Any]
    kind: str = "callable"


class NetworkGuard:
    """Process-lifetime audit hook. This is not an OS or native-code sandbox."""

    def __init__(self) -> None:
        self.violations = 0

    def install(self) -> None:
        sys.addaudithook(self._audit)

    def _audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event in {
            "socket.connect",
            "socket.bind",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.sendto",
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
            "os.exec",
            "ctypes.dlopen",
            "ctypes.dlsym",
        }:
            self.violations += 1
            raise RewindError("replay guard blocked an external operation")


def replay_file(
    path: str | Path, factory: str, *, timeout: float = 30, isolation: str = "python-guard"
) -> ReplayReport:
    """Replay in a fresh interpreter with a finite parent-enforced timeout."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if isolation not in {"python-guard", "adapter-only"}:
        raise ValueError("unknown isolation mode")
    command = [
        sys.executable,
        "-m",
        "rewind._worker",
        str(Path(path).resolve()),
        factory,
        isolation,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return ReplayReport("replay_error", "replay worker exceeded timeout", isolation=isolation)
    if result.returncode != 0:
        return ReplayReport(
            "replay_error", "replay worker exited unexpectedly", isolation=isolation
        )
    try:
        data = json.loads(result.stdout)
        return ReplayReport(**data)
    except (ValueError, TypeError):
        return ReplayReport("replay_error", "invalid worker report", isolation=isolation)
