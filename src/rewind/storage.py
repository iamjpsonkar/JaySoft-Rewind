"""Private, bounded, atomic local storage. No application imports on load."""

import math
import os
import stat
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
        if (
            type(max_bytes) is not int
            or max_bytes <= 0
            or type(retention_seconds) not in (int, float)
            or not math.isfinite(retention_seconds)
            or retention_seconds <= 0
        ):
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

    def save(self, snapshot: Snapshot, *, overwrite: bool = True) -> Path:
        snapshot = Snapshot.from_bytes(snapshot.raw, self.limits)
        if len(snapshot.raw) > self.max_bytes:
            raise RewindError("snapshot exceeds storage quota")
        with self._lock:
            target = self._path(snapshot.id)
            files = sorted(self.path.glob("*.rewind.json"), key=lambda p: p.lstat().st_mtime)
            entries = [(p, p.lstat()) for p in files]
            if any(not stat.S_ISREG(info.st_mode) for _, info in entries):
                raise RewindError("non-regular artifact found in snapshot store")
            # Replacing an existing ID consumes only its new size, not both copies.
            total = sum(info.st_size for p, info in entries if p != target)
            evict = []
            now = time.time()
            for p, info in entries:
                if p == target:
                    continue
                if (
                    now - info.st_mtime > self.retention_seconds
                    or total + len(snapshot.raw) > self.max_bytes
                ):
                    evict.append(p)
                    total -= info.st_size
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.path)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(snapshot.raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if overwrite:
                    os.replace(temporary, target)
                else:
                    # Atomic no-clobber publication; import must not replace evidence.
                    os.link(temporary, target)
                # Keep existing artifacts intact if writing or publication fails.
                for p in evict:
                    p.unlink()
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
    # O_NONBLOCK prevents a crafted FIFO from hanging before fstat can reject it.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InvalidSnapshot("artifact must be a regular file")
        raw = stream.read(limits.snapshot_bytes + 1)
    return Snapshot.from_bytes(raw, limits)
