"""Private, bounded, atomic local storage. No application imports on load."""

import math
import os
import stat
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import InvalidSnapshot, RewindError
from .limits import Limits
from .snapshot import ID_PATTERN, Snapshot


class LocalStore:
    _suffix = ".rewind.json"

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
        self._pid = os.getpid()
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

    @contextmanager
    def _locked(self) -> Iterator[int | None]:
        # A child must not acquire a threading lock inherited while held by a
        # vanished parent thread. Each process opens its own directory descriptor.
        if self._pid != os.getpid():
            self._lock = threading.Lock()
            self._pid = os.getpid()
        with self._lock:
            if os.name != "posix":
                yield None
                return
            import fcntl

            fd = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                info = os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                    raise RewindError("store directory must remain private")
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield fd
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _sync_directory(fd: int | None) -> None:
        if fd is not None:
            os.fsync(fd)

    def _entries(self) -> list[tuple[Path, os.stat_result]]:
        entries = [(path, path.lstat()) for path in self.path.glob(f"*{self._suffix}")]
        if any(not stat.S_ISREG(info.st_mode) for _, info in entries):
            raise RewindError("non-regular artifact found in snapshot store")
        return sorted(entries, key=lambda entry: (entry[1].st_mtime_ns, entry[0].name))

    def _path(self, snapshot_id: str) -> Path:
        if type(snapshot_id) is not str or not ID_PATTERN.fullmatch(snapshot_id):
            raise InvalidSnapshot("invalid snapshot ID")
        return self.path / f"{snapshot_id}{self._suffix}"

    def save(self, snapshot: Snapshot, *, overwrite: bool = True) -> Path:
        snapshot = Snapshot.from_bytes(snapshot.raw, self.limits)
        return self._save_bytes(snapshot.id, snapshot.raw, overwrite=overwrite)

    def _save_bytes(self, snapshot_id: str, raw: bytes, *, overwrite: bool = True) -> Path:
        if type(overwrite) is not bool:
            raise ValueError("overwrite must be a boolean")
        if len(raw) > self.max_bytes:
            raise RewindError("snapshot exceeds storage quota")
        with self._locked() as directory:
            target = self._path(snapshot_id)
            entries = self._entries()
            # Replacing an existing ID consumes only its new size, not both copies.
            total = sum(info.st_size for p, info in entries if p != target)
            evict = []
            now = time.time()
            for p, info in entries:
                if p == target:
                    continue
                if (
                    now - info.st_mtime > self.retention_seconds
                    or total + len(raw) > self.max_bytes
                ):
                    evict.append(p)
                    total -= info.st_size
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.path)
            published = False
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if overwrite:
                    os.replace(temporary, target)
                else:
                    # Atomic no-clobber publication; import must not replace evidence.
                    os.link(temporary, target)
                published = True
                # Persist the new directory entry before evicting old evidence.
                self._sync_directory(directory)
                # Keep existing artifacts intact if writing or publication fails.
                for p in evict:
                    p.unlink()
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
                if published:
                    self._sync_directory(directory)
            return target

    def load(self, snapshot_id: str) -> Snapshot:
        with self._locked():
            snapshot = load_file(self._path(snapshot_id), self.limits)
        if snapshot.id != snapshot_id:
            raise InvalidSnapshot("snapshot ID differs from its storage name")
        return snapshot

    def ids(self) -> list[str]:
        with self._locked():
            return sorted(
                path.name.removesuffix(self._suffix)
                for path in self.path.glob(f"*{self._suffix}")
                if stat.S_ISREG(path.lstat().st_mode)
                and ID_PATTERN.fullmatch(path.name.removesuffix(self._suffix))
            )

    def delete(self, snapshot_id: str) -> None:
        with self._locked() as directory:
            path = self._path(snapshot_id)
            if not stat.S_ISREG(path.lstat().st_mode):
                raise RewindError("refusing non-regular artifact")
            path.unlink()
            self._sync_directory(directory)

    def prune(self) -> dict[str, int]:
        """Apply TTL and quota explicitly, including when no new captures arrive."""
        with self._locked() as directory:
            entries = self._entries()
            total = sum(info.st_size for _, info in entries)
            removed = removed_bytes = 0
            now = time.time()
            try:
                for path, info in entries:
                    if now - info.st_mtime > self.retention_seconds or total > self.max_bytes:
                        path.unlink()
                        total -= info.st_size
                        removed += 1
                        removed_bytes += info.st_size
            finally:
                if removed:
                    self._sync_directory(directory)
            return {
                "removed": removed, "removed_bytes": removed_bytes,
                "remaining_items": len(entries) - removed, "remaining_bytes": total,
            }


def load_file(path: str | Path, limits: Limits | None = None) -> Snapshot:
    limits = limits or Limits()
    return Snapshot.from_bytes(_read_file(path, limits.snapshot_bytes), limits)


def _read_file(path: str | Path, max_bytes: int) -> bytes:
    # O_NONBLOCK prevents a crafted FIFO from hanging before fstat can reject it.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InvalidSnapshot("artifact must be a regular file")
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise InvalidSnapshot("artifact exceeds byte limit")
    return raw
