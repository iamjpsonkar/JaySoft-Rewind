"""Bounded data-only portable archives. Nothing is extracted or imported as code."""

import hashlib
import io
import os
import stat
import tempfile
import zipfile
from pathlib import Path

from .codecs import dumps, loads
from .errors import InvalidSnapshot
from .limits import Limits
from .snapshot import Snapshot
from .storage import LocalStore, load_file


def export_snapshot(source: str | Path, output: str | Path, limits: Limits | None = None) -> Path:
    limits = limits or Limits()
    snapshot = load_file(source, limits)
    manifest = dumps(
        {
            "format": "rewind-archive-1",
            "snapshot_id": snapshot.id,
            "sha256": hashlib.sha256(snapshot.raw).hexdigest(),
            "bytes": len(snapshot.raw),
        }
    )
    output = Path(output)
    fd, temporary = tempfile.mkstemp(prefix=".rewind-export-", dir=output.parent)
    try:
        with os.fdopen(fd, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("manifest.json", manifest)
                archive.writestr("snapshot.rewind.json", snapshot.raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        os.unlink(temporary)
    return output


def read_archive(source: str | Path, limits: Limits | None = None) -> Snapshot:
    limits = limits or Limits()
    budget = limits.snapshot_bytes + 4096
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(source, flags)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InvalidSnapshot("archive must be a regular file")
        raw = stream.read(budget + 1)
    if len(raw) > budget:
        raise InvalidSnapshot("archive exceeds byte limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) != 2 or {e.filename for e in entries} != {
                "manifest.json",
                "snapshot.rewind.json",
            }:
                raise InvalidSnapshot("unsupported archive members")
            for entry in entries:
                bound = 1024 if entry.filename == "manifest.json" else limits.snapshot_bytes
                if (
                    entry.compress_type != zipfile.ZIP_STORED
                    or entry.flag_bits & 1
                    or entry.file_size > bound
                    or entry.compress_size != entry.file_size
                    or stat.S_ISLNK(entry.external_attr >> 16)
                ):
                    raise InvalidSnapshot("unsupported or oversized archive member")
            manifest = loads(archive.read("manifest.json"), limits)
            payload = archive.read("snapshot.rewind.json")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        raise InvalidSnapshot("invalid portable archive") from exc
    if (
        type(manifest) is not dict
        or set(manifest) != {"format", "snapshot_id", "sha256", "bytes"}
        or manifest["format"] != "rewind-archive-1"
        or type(manifest["bytes"]) is not int
        or manifest["bytes"] != len(payload)
        or manifest["sha256"] != hashlib.sha256(payload).hexdigest()
    ):
        raise InvalidSnapshot("archive manifest mismatch")
    snapshot = Snapshot.from_bytes(payload, limits)
    if snapshot.id != manifest["snapshot_id"]:
        raise InvalidSnapshot("archive identity mismatch")
    return snapshot


def import_snapshot(source: str | Path, store: LocalStore) -> Path:
    """Validate bounded archive bytes, then atomically store without replacing an ID."""
    return store.save(read_archive(source, store.limits), overwrite=False)
