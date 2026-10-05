"""Explicit SQLite DB-API boundary; replay never opens a database connection."""

import math
import sqlite3
import weakref
from collections.abc import Callable, Iterator
from typing import Any

from .. import context
from ..codecs import decode
from ..errors import CaptureLimit, RewindError
from ..recorder import Recorder
from ..replay import ReplaySession

_CONNECTION_IDS: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()

_ERRORS = {
    f"sqlite3.{name}": getattr(sqlite3, name)
    for name in (
        "Error",
        "Warning",
        "InterfaceError",
        "DatabaseError",
        "DataError",
        "OperationalError",
        "IntegrityError",
        "InternalError",
        "ProgrammingError",
        "NotSupportedError",
    )
}


def _active() -> Recorder | ReplaySession | None:
    active = context.current.get()
    return None if isinstance(active, Recorder) and active.sealed else active


def _normalize(value: Any, active: Recorder | ReplaySession) -> Any:
    """Normalize SQLite blob bindings without consuming arbitrary iterators."""
    count = 0
    size = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal count, size
        count += 1
        if count > active.limits.items or depth > active.limits.depth:
            raise CaptureLimit("database argument structural limit")
        if type(item) in (memoryview, bytearray):
            size += item.nbytes if type(item) is memoryview else len(item)
            if size > active.limits.snapshot_bytes:
                raise CaptureLimit("database argument byte limit")
            return bytes(item)
        if type(item) in (list, tuple):
            result = [visit(part, depth + 1) for part in item]
            return tuple(result) if type(item) is tuple else result
        if type(item) is dict:
            return {key: visit(part, depth + 1) for key, part in item.items()}
        return item

    return visit(value, 0)


def _call(dependency: str, target: str, method: str, args: Any, live: Callable[[], Any]) -> Any:
    active = _active()
    request = {"target": target, "method": method, "args": args}
    if isinstance(active, ReplaySession):
        try:
            request = _normalize(request, active)
        except Exception:
            active.fail("database input is outside supported replay scope")
        outcome = active.consume("db.call", dependency, request)
        try:
            value = decode(outcome["value"], active.limits)
        except (RewindError, KeyError, TypeError):
            active.fail("invalid recorded database result")
        if type(value) is not dict or set(value) not in (
            {"result"},
            {"error", "args", "code", "name"},
        ):
            active.fail("invalid recorded database envelope")
        if "result" in value:
            return value["result"]
        cls = _ERRORS.get(value["error"]) if type(value["error"]) is str else None
        if (
            cls is None
            or type(value["args"]) is not tuple
            or any(type(item) is not str for item in value["args"])
            or (value["code"] is not None and type(value["code"]) is not int)
            or (value["name"] is not None and type(value["name"]) is not str)
        ):
            active.fail("unsupported recorded database exception")
        error = cls(*value["args"])
        if value["code"] is not None:
            error.sqlite_errorcode = value["code"]
        if value["name"] is not None:
            error.sqlite_errorname = value["name"]
        raise error
    slot = None
    if isinstance(active, Recorder):
        try:
            slot = active.begin("db.call", dependency, _normalize(request, active))
        except Exception:
            active.mark("database_input_capture_failed")
    try:
        result = live()
    except BaseException as exc:
        if isinstance(active, Recorder):
            try:
                kind = f"{type(exc).__module__}.{type(exc).__qualname__}"
                if kind not in _ERRORS:
                    active.mark("unsupported_database_exception")
                if not active.policy.exception_args:
                    active.mark("exception_args_excluded")
                args = exc.args if active.policy.exception_args else ()
                active.finish(
                    slot,
                    active.returned(
                        {
                            "error": kind,
                            "args": args,
                            "code": getattr(exc, "sqlite_errorcode", None),
                            "name": getattr(exc, "sqlite_errorname", None),
                        }
                    ),
                )
            except Exception:
                active.mark("database_output_capture_failed")
        raise
    if isinstance(active, Recorder):
        try:
            active.finish(slot, active.returned({"result": result}))
        except Exception:
            active.mark("database_output_capture_failed")
    return result


class RecordingSQLite:
    """A sqlite3-compatible connector with explicit dependency identity.

    Connect inside the captured entry point. Connection options are part of the
    strict trace; the database path is replaced by the logical dependency name.
    """

    def __init__(self, *, dependency: str = "database") -> None:
        if type(dependency) is not str or not dependency:
            raise ValueError("dependency must not be empty")
        self.dependency = dependency

    def connect(self, database: str, **kwargs: Any) -> "Connection":
        connection: Any = None

        def live() -> int:
            nonlocal connection
            connection = sqlite3.connect(database, **kwargs)
            active = _active()
            identifier = _CONNECTION_IDS.get(active, 0) + 1 if active is not None else 1
            if active is not None:
                _CONNECTION_IDS[active] = identifier
            return identifier

        # Options such as factories cannot be serialized; they leave an ineligible
        # recording while the application retains its ordinary driver behavior.
        identifier = _call(self.dependency, "database", "connect", kwargs, live)
        if type(identifier) is not int or identifier < 1:
            active = _active()
            if isinstance(active, ReplaySession):
                active.fail("invalid recorded database connection identifier")
            raise RuntimeError("invalid database connection identifier")
        return Connection(connection, self.dependency, identifier)


class Connection:
    def __init__(self, inner: Any, dependency: str, identifier: int) -> None:
        self._inner = inner
        self._dependency = dependency
        self._target = f"connection:{identifier}"
        owner = _active()
        self._owner = weakref.ref(owner) if owner is not None else lambda: None
        self._cursors = 0

    def _live(self) -> Any:
        if self._inner is None:
            raise RuntimeError("replay database connection cannot perform live operations")
        return self._inner

    def _call(self, method: str, args: Any, live: Callable[[], Any]) -> Any:
        self._check_owner()
        return _call(self._dependency, self._target, method, args, live)

    def _check_owner(self) -> None:
        active = _active()
        if active is not None and active is not self._owner():
            if isinstance(active, ReplaySession):
                active.fail("database connection was created outside replay")
            active.mark("database_connection_outside_capture")

    def cursor(self, *args: Any, **kwargs: Any) -> "Cursor":
        inner: Any = None

        def live() -> None:
            nonlocal inner
            inner = self._live().cursor(*args, **kwargs)

        self._call("cursor", {"args": args, "kwargs": kwargs}, live)
        self._cursors += 1
        return Cursor(self, inner, self._cursors)

    def execute(self, sql: str, parameters: Any = ()) -> "Cursor":
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any) -> "Cursor":
        return self.cursor().executemany(sql, parameters)

    def commit(self) -> None:
        self._call("commit", (), lambda: self._live().commit())

    def rollback(self) -> None:
        self._call("rollback", (), lambda: self._live().rollback())

    def close(self) -> None:
        self._call("close", (), lambda: self._live().close())

    def __enter__(self) -> "Connection":
        def live() -> None:
            self._live().__enter__()

        self._call("enter", (), live)
        return self

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> Any:
        return self._call(
            "exit",
            {"failed": kind is not None},
            lambda: self._live().__exit__(kind, value, traceback),
        )

    @property
    def in_transaction(self) -> bool:
        return bool(self._call("in_transaction", (), lambda: self._live().in_transaction))

    @property
    def isolation_level(self) -> Any:
        return self._call("isolation_level", (), lambda: self._live().isolation_level)

    @isolation_level.setter
    def isolation_level(self, value: Any) -> None:
        self._call(
            "set_isolation_level", value, lambda: setattr(self._live(), "isolation_level", value)
        )

    def create_function(self, name: str, narg: int, function: Any, **kwargs: Any) -> Any:
        active = _active()
        # SQLAlchemy's two built-in SQLite registrations are deterministic setup.
        # Arbitrary Python UDFs can perform untraced I/O and remain unsupported.
        supported = (name == "floor" and narg == 1 and function is math.floor) or (
            name == "regexp"
            and narg == 2
            and getattr(function, "__module__", "") == "sqlalchemy.dialects.sqlite.pysqlite"
        )
        if not supported:
            if isinstance(active, ReplaySession):
                active.fail("custom database functions are unsupported")
            if isinstance(active, Recorder):
                active.mark("database_function_unsupported")
        return self._call(
            "create_function",
            {"name": name, "narg": narg, "options": kwargs},
            lambda: self._live().create_function(name, narg, function, **kwargs),
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name == "isolation_level":
            object.__setattr__(self, name, value)
            return
        active = _active()
        if isinstance(active, ReplaySession):
            active.fail("unsupported database connection setting")
        if isinstance(active, Recorder):
            active.mark("database_connection_setting_unsupported")
        setattr(self._live(), name, value)

    def __getattr__(self, name: str) -> Any:
        active = _active()
        if isinstance(active, ReplaySession):
            active.fail("unsupported database connection operation")
        if isinstance(active, Recorder):
            active.mark("database_connection_operation_unsupported")
        return getattr(self._live(), name)


class Cursor(Iterator[Any]):
    def __init__(self, connection: Connection, inner: Any, identifier: int) -> None:
        self._connection = connection
        self._inner = inner
        self._target = f"{connection._target}/cursor:{identifier}"

    @property
    def connection(self) -> Connection:
        return self._connection

    def _live(self) -> Any:
        if self._inner is None:
            raise RuntimeError("replay database cursor cannot perform live operations")
        return self._inner

    def _call(self, method: str, args: Any, live: Callable[[], Any]) -> Any:
        self.connection._check_owner()
        return _call(self.connection._dependency, self._target, method, args, live)

    def execute(self, sql: str, parameters: Any = ()) -> "Cursor":
        def live() -> None:
            self._live().execute(sql, parameters)

        self._call("execute", {"sql": sql, "parameters": parameters}, live)
        return self

    def executemany(self, sql: str, parameters: Any) -> "Cursor":
        # Never drain a generator merely to capture it. A generator/custom object
        # is deliberately ineligible, but still consumed only by the real driver.
        def live() -> None:
            self._live().executemany(sql, parameters)

        self._call("executemany", {"sql": sql, "parameters": parameters}, live)
        return self

    def fetchone(self) -> Any:
        return self._call("fetchone", (), lambda: self._live().fetchone())

    def fetchmany(self, size: int | None = None) -> Any:
        args = () if size is None else (size,)
        return self._call("fetchmany", args, lambda: self._live().fetchmany(*args))

    def fetchall(self) -> Any:
        return self._call("fetchall", (), lambda: self._live().fetchall())

    def __iter__(self) -> "Cursor":
        return self

    def __next__(self) -> Any:
        def live() -> dict[str, Any]:
            try:
                return {"done": False, "row": next(self._live())}
            except StopIteration:
                return {"done": True, "row": None}

        value = self._call("next", (), live)
        if (
            type(value) is not dict
            or set(value) != {"done", "row"}
            or type(value["done"]) is not bool
        ):
            active = _active()
            if isinstance(active, ReplaySession):
                active.fail("invalid recorded database iterator result")
            raise RuntimeError("invalid database iterator result")
        if value["done"]:
            raise StopIteration
        return value["row"]

    def close(self) -> None:
        self._call("close", (), lambda: self._live().close())

    @property
    def description(self) -> Any:
        return self._call("description", (), lambda: self._live().description)

    @property
    def rowcount(self) -> int:
        return self._call("rowcount", (), lambda: self._live().rowcount)

    @property
    def lastrowid(self) -> Any:
        return self._call("lastrowid", (), lambda: self._live().lastrowid)

    @property
    def arraysize(self) -> int:
        return self._call("arraysize", (), lambda: self._live().arraysize)

    @arraysize.setter
    def arraysize(self, size: int) -> None:
        self._call("set_arraysize", size, lambda: setattr(self._live(), "arraysize", size))

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name == "arraysize":
            object.__setattr__(self, name, value)
            return
        active = _active()
        if isinstance(active, ReplaySession):
            active.fail("unsupported database cursor setting")
        if isinstance(active, Recorder):
            active.mark("database_cursor_setting_unsupported")
        setattr(self._live(), name, value)

    def __getattr__(self, name: str) -> Any:
        active = _active()
        if isinstance(active, ReplaySession):
            active.fail("unsupported database cursor operation")
        if isinstance(active, Recorder):
            active.mark("database_cursor_operation_unsupported")
        return getattr(self._live(), name)
