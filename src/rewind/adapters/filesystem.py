"""Explicit synchronous filesystem observations under a developer-selected root."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from .. import context
from ..recorder import Recorder
from ..replay import ReplaySession

T = TypeVar("T")
_EXCEPTIONS = {
    f"builtins.{cls.__name__}": cls
    for cls in (
        FileNotFoundError,
        FileExistsError,
        PermissionError,
        IsADirectoryError,
        NotADirectoryError,
        OSError,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        UnicodeEncodeError,
    )
}


class RecordingFilesystem:
    """Record selected calls without patching builtins or touching disk during replay.

    The root is a trusted local directory, not a hostile-code sandbox. Relative
    paths cannot contain '..'; existing symlink components are rejected. Callers
    must prevent concurrent replacement of path components by other processes.
    """

    def __init__(self, root: str | Path, *, dependency: str = "filesystem") -> None:
        self.root = Path(root).absolute()
        self.dependency = dependency

    def _path(self, name: str) -> Path:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("filesystem path must remain under the configured root")
        path = self.root
        if path.is_symlink():
            raise ValueError("filesystem root cannot be a symlink")
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("filesystem paths cannot traverse symlinks")
        return path

    def _call(self, method: str, name: str, args: dict[str, Any], live: Callable[[], T]) -> T:
        # Lexical validation is also enforced during replay, without filesystem I/O.
        if type(name) is not str or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("filesystem path must be a relative string without '..'")
        active = context.current.get()
        parameters = {"method": method, "path": name, **args}
        if isinstance(active, ReplaySession):
            outcome = active.consume("filesystem.call", self.dependency, parameters)
            if outcome["kind"] == "return":
                return active.unpack(outcome["value"])
            kind = _EXCEPTIONS.get(outcome["type"])
            values = active.unpack(outcome["args"])
            if kind is None or type(values) is not tuple:
                active.fail("unsupported filesystem exception")
            raise kind(*values)
        if not isinstance(active, Recorder) or active.sealed:
            return live()
        slot = active.begin("filesystem.call", self.dependency, parameters)
        try:
            result = live()
        except BaseException as exc:
            exception_name = f"{type(exc).__module__}.{type(exc).__qualname__}"
            if exception_name not in _EXCEPTIONS:
                active.mark("filesystem_exception_unsupported")
            # OSError filenames are absolute and potentially private. Record only
            # errno/message; exceptions carrying filename attributes are ineligible.
            if isinstance(exc, OSError) and exc.filename is not None:
                active.mark("filesystem_exception_path_omitted")
            active.finish(slot, active.raised(exc))
            raise
        active.finish(slot, active.returned(result))
        return result

    def read_bytes(self, path: str) -> bytes:
        return self._call("read_bytes", path, {}, lambda: self._path(path).read_bytes())

    def read_text(self, path: str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self._call(
            "read_text",
            path,
            {"encoding": encoding, "errors": errors},
            lambda: self._path(path).read_text(encoding=encoding, errors=errors),
        )

    def write_bytes(self, path: str, data: bytes) -> int:
        return self._call(
            "write_bytes", path, {"data": data}, lambda: self._path(path).write_bytes(data)
        )

    def write_text(self, path: str, data: str, *, encoding: str = "utf-8") -> int:
        return self._call(
            "write_text",
            path,
            {"data": data, "encoding": encoding},
            lambda: self._path(path).write_text(data, encoding=encoding),
        )

    def exists(self, path: str) -> bool:
        return self._call("exists", path, {}, lambda: self._path(path).exists())

    def listdir(self, path: str = ".") -> list[str]:
        return self._call("listdir", path, {}, lambda: os.listdir(self._path(path)))

    def unlink(self, path: str, *, missing_ok: bool = False) -> None:
        return self._call(
            "unlink",
            path,
            {"missing_ok": missing_ok},
            lambda: self._path(path).unlink(missing_ok=missing_ok),
        )
