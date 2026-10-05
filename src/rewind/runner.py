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
        # stdlib ctypes initializes PyDLL(None) once. Prepare that interpreter
        # handle before enforcing native-load denial, so psycopg binary imports
        # can use ctypes types without opening a library after guard installation.
        import ctypes

        _ = ctypes.pythonapi
        sys.addaudithook(self._audit)

    def _audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event in {
            "socket.connect",
            "sqlite3.connect",  # Prevent changed code from bypassing the DBAPI replay adapter.
            "socket.bind",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.getnameinfo",
            "socket.sendto",
            "socket.sendmsg",  # Datagram sends do not require a prior connect().
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
    data = _run_worker(path, factory, timeout=timeout, isolation=isolation)
    try:
        return ReplayReport(**data)
    except (ValueError, TypeError):
        return ReplayReport("replay_error", "invalid worker report", isolation=isolation)


def _run_worker(
    path: str | Path,
    factory: str,
    *,
    timeout: float,
    isolation: str,
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    request = None
    if comparison is not None:
        from .codecs import dumps

        command.append("compare")
        request = dumps(comparison)
    try:
        result = subprocess.run(
            command, input=request, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return ReplayReport(
            "replay_error", "replay worker exceeded timeout", isolation=isolation
        ).to_dict()
    if result.returncode != 0:
        return ReplayReport(
            "replay_error", "replay worker exited unexpectedly", isolation=isolation
        ).to_dict()
    try:
        data = json.loads(result.stdout)
        if type(data) is not dict:
            raise ValueError("invalid worker report")
        return data
    except (ValueError, TypeError):
        return ReplayReport("replay_error", "invalid worker report", isolation=isolation).to_dict()
