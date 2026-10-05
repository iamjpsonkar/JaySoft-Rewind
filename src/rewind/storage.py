"""Private, bounded, atomic local storage. No application imports on load."""

import os
import tempfile
import threading
import time
from pathlib import Path

from .errors import InvalidSnapshot, RewindError
from .limits import Limits
from .snapshot import ID_PATTERN, Snapshot


class LocalStore:
    def __init__(
        self,
        path: str | Path = ".rewind/snapshots",
        *,
        max_bytes: int = 256 * 1024 * 1024,
        retention_seconds: float = 86400,
        limits: Limits | None = None,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.retention_seconds = retention_seconds
        self.limits = limits or Limits()
        self._lock = threading.Lock()
        if max_bytes <= 0 or retention_seconds <= 0:
            raise ValueError("storage limits must be positive")
        if self.path.is_symlink():
            raise ValueError("store directory cannot be a symlink")
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.stat().st_mode & 0o077:
            raise ValueError("store directory must be private (chmod 700)")

    def _path(self, snapshot_id: str) -> Path:
        if not ID_PATTERN.fullmatch(snapshot_id):
            raise InvalidSnapshot("invalid snapshot ID")
        return self.path / f"{snapshot_id}.rewind.json"

    def save(self, snapshot: Snapshot) -> Path:
        snapshot = Snapshot.from_bytes(snapshot.raw, self.limits)
        if len(snapshot.raw) > self.max_bytes:
            raise RewindError("snapshot exceeds storage quota")
        with self._lock:
            files = sorted(self.path.glob("*.rewind.json"), key=lambda p: p.lstat().st_mtime)
            total = sum(p.lstat().st_size for p in files)
            for p in files:
                if p.is_symlink():
                    raise RewindError("symlink found in snapshot store")
                stat = p.stat()
                if (
                    time.time() - stat.st_mtime > self.retention_seconds
                    or total + len(snapshot.raw) > self.max_bytes
                ):
                    p.unlink()
                    total -= stat.st_size
            target = self._path(snapshot.id)
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.path)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(snapshot.raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return target

    def load(self, snapshot_id: str) -> Snapshot:
        return load_file(self._path(snapshot_id), self.limits)

    def ids(self) -> list[str]:
        return sorted(
            p.name.removesuffix(".rewind.json")
            for p in self.path.glob("*.rewind.json")
            if not p.is_symlink() and ID_PATTERN.fullmatch(p.name.removesuffix(".rewind.json"))
        )

    def delete(self, snapshot_id: str) -> None:
        path = self._path(snapshot_id)
        if path.is_symlink():
            raise RewindError("refusing symlink artifact")
        path.unlink()


def load_file(path: str | Path, limits: Limits | None = None) -> Snapshot:
    limits = limits or Limits()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(limits.snapshot_bytes + 1)
    return Snapshot.from_bytes(raw, limits)
