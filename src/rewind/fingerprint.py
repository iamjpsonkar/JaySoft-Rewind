"""Fingerprint explicitly declared application sources and adapter versions."""

import hashlib
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def fingerprint(name: str, code_paths: list[str | Path]) -> dict[str, Any]:
    if not name or not code_paths:
        raise ValueError("application name and code_paths are required")
    digest = hashlib.sha256()
    total = 0
    for index, raw_path in enumerate(code_paths):
        path = Path(raw_path)
        if not path.exists() or path.is_symlink():
            raise ValueError("code_paths must exist and cannot be symlinks")
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        files = [
            f
            for f in files
            if not any(
                p.startswith(".") or p == "__pycache__"
                for p in f.relative_to(path if path.is_dir() else path.parent).parts
            )
        ]
        if not files:
            raise ValueError("code path has no Python source files")
        for file in files:
            if file.is_symlink():
                raise ValueError("source files cannot be symlinks")
            raw = file.read_bytes()
            total += len(raw)
            if total > 16 * 1024 * 1024:
                raise ValueError("declared source set exceeds 16 MiB")
            relative = file.relative_to(path if path.is_dir() else path.parent).as_posix()
            digest.update(f"{index}:{relative}:{len(raw)}:".encode())
            digest.update(raw)
    dependencies = {}
    for dependency in ("httpx", "fastapi", "starlette", "sqlalchemy", "redis", "flask", "werkzeug"):
        try:
            dependencies[dependency] = version(dependency)
        except PackageNotFoundError:
            pass
    return {
        "name": name,
        "code_digest": digest.hexdigest(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "dependencies": dependencies,
    }
