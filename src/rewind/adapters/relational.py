"""Shared explicit synchronous relational DBAPI capture boundary."""

import re
from collections.abc import Callable
from typing import Any, NamedTuple, TypeVar, cast

from ..codecs import decode
from ..errors import CaptureLimit
from ..recorder import Recorder
from ..replay import ReplaySession
from .dbapi import _CONNECTION_IDS, _active, _normalize
from .dbapi import Connection as SQLiteConnection
from .dbapi import Cursor as SQLiteCursor

C = TypeVar("C", bound="Connection")


class Driver:
    def __init__(self, name: str, errors: dict[str, type[Exception]]) -> None:
        self.name = name
        self.errors = errors

    def error_data(self, error: BaseException) -> dict[str, Any]:
        return {"type": f"{type(error).__module__}.{type(error).__qualname__}", "args": error.args}

    def restore_error(self, data: Any, active: ReplaySession) -> Exception:
        if (
            type(data) is not dict
            or set(data) != {"type", "args"}
            or type(data["type"]) is not str
            or type(data["args"]) is not tuple
            or any(type(arg) not in (str, int) for arg in data["args"])
        ):
            active.fail("invalid relational database exception")
        cls = self.errors.get(data["type"])
        if cls is None:
            active.fail("unsupported relational database exception")
        return cls(*data["args"])

    def call(
        self,
        dependency: str,
        target: str,
        method: str,
        args: Any,
        live: Callable[[], Any],
        captured: Callable[[Any, Recorder], Any] | None = None,
    ) -> Any:
        active = _active()
        request = {"driver": self.name, "target": target, "method": method, "args": args}
        if isinstance(active, ReplaySession):
            try:
                request = _normalize_relational(request, active)
            except Exception:
                active.fail("database input exceeds supported replay scope")
            outcome = active.consume("db.call", dependency, request)
            try:
                value = decode(outcome["value"], active.limits)
            except Exception:
                active.fail("invalid relational database outcome")
            if type(value) is not dict or set(value) not in ({"result"}, {"error"}):
                active.fail("invalid relational database envelope")
            if "error" in value:
                raise self.restore_error(value["error"], active)
            return value["result"]
        slot = None
        if isinstance(active, Recorder):
            try:
                incoming = _normalize_relational(request, active)
                sql = args.get("sql") if type(args) is dict else None
                if (
                    type(sql) is str
                    and len(sql) <= active.limits.snapshot_bytes
                    and any(
                        active.policy.is_sensitive(word)
                        for word in re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", sql)
                    )
                ):
                    incoming["args"] = {"sql": "[REDACTED]", "parameters": None}
                    active.mark("sensitive_database_query_removed")
                slot = active.begin("db.call", dependency, incoming)
            except Exception:
                active.mark("database_input_capture_failed")
        try:
            result = live()
        except BaseException as error:
            if isinstance(active, Recorder):
                try:
                    kind = f"{type(error).__module__}.{type(error).__qualname__}"
                    if kind not in self.errors:
                        active.mark("unsupported_database_exception")
                    if not active.policy.exception_args or method == "connect":
                        # Do not inspect diagnostic fields when policy excludes
                        # them. Connection errors can echo sensitive DSN values.
                        active.mark("database_exception_details_excluded")
                        data = {"type": kind, "args": ()}
                    else:
                        data = self.error_data(error)
                    active.finish(slot, active.returned({"error": data}))
                except Exception:
                    active.mark("database_output_capture_failed")
            raise
        if isinstance(active, Recorder):
            try:
                value = captured(result, active) if captured else result
                active.finish(slot, active.returned({"result": value}))
            except Exception:
                active.mark("database_output_capture_failed")
        return result

    def connect(
        self,
        dependency: str,
        options: dict[str, Any],
        live: Callable[[], Any],
        connection_type: type[C],
    ) -> C:
        if type(dependency) is not str or not dependency:
            raise ValueError("dependency must not be empty")
        inner = None

        def opened() -> int:
            nonlocal inner
            inner = live()
            active = _active()
            identifier = _CONNECTION_IDS.get(active, 0) + 1 if active is not None else 1
            if active is not None:
                _CONNECTION_IDS[active] = identifier
            return identifier

        identifier = self.call(dependency, "database", "connect", options, opened)
        if type(identifier) is not int or identifier < 1:
            active = _active()
            if isinstance(active, ReplaySession):
                active.fail("invalid relational connection identifier")
            raise RuntimeError("invalid connection identifier")
        return connection_type(inner, dependency, identifier, self)


def _normalize_relational(value: Any, active: Recorder | ReplaySession) -> Any:
    """Normalize known SQLAlchemy data objects inside the bounded capture path."""
    count = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > active.limits.items or depth > active.limits.depth:
            raise CaptureLimit("relational input structural limit")
        if type(item).__name__ == "immutabledict" and type(item).__module__.startswith(
            "sqlalchemy."
        ):
            from sqlalchemy.util import immutabledict

            if type(item) is immutabledict:
                if len(item) > active.limits.items:
                    raise CaptureLimit("relational mapping limit")
                item = dict(item)
        if type(item) is dict:
            result = {}
            for key, part in item.items():
                if isinstance(key, str) and type(key).__module__ == "sqlalchemy.sql.elements":
                    key = str.__str__(key)
                result[key] = visit(part, depth + 1)
            return result
        if type(item) in (tuple, list):
            result_list = [visit(part, depth + 1) for part in item]
            return tuple(result_list) if type(item) is tuple else result_list
        return item

    return _normalize(visit(value, 0), active)


def unsupported(reason: str) -> None:
    active = _active()
    if isinstance(active, ReplaySession):
        active.fail(reason)
    if isinstance(active, Recorder):
        active.mark(reason)


class Connection(SQLiteConnection):
    def __init__(self, inner: Any, dependency: str, identifier: int, driver: Driver) -> None:
        super().__init__(inner, dependency, identifier)
        self._driver = driver

    def _call(self, method: str, args: Any, live: Callable[[], Any]) -> Any:
        if self._retired_cleanup(method):
            return None
        self._check_owner()
        return self._driver.call(self._dependency, self._target, method, args, live)

    def cursor(self, *args: Any, **kwargs: Any) -> "Cursor":
        inner = None
        if args or kwargs:
            unsupported("custom_database_cursor_unsupported")

        def live() -> None:
            nonlocal inner
            inner = self._live().cursor(*args, **kwargs)

        self._call("cursor", {"args": args, "kwargs": kwargs}, live)
        self._cursors += 1
        return Cursor(self, inner, self._cursors)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or isinstance(getattr(type(self), name, None), property):
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)


class Column(NamedTuple):
    name: Any
    type_code: Any
    display_size: Any
    internal_size: Any
    precision: Any
    scale: Any
    null_ok: Any


class Cursor(SQLiteCursor):
    @property
    def connection(self) -> Connection:
        return cast(Connection, self._connection)

    def _call(self, method: str, args: Any, live: Callable[[], Any]) -> Any:
        if method == "close" and self.connection._retired_cleanup(method):
            return None
        self.connection._check_owner()
        return self.connection._driver.call(
            self.connection._dependency,
            self._target,
            method,
            args,
            live,
            (lambda result, active: self._redact_rows(method, result, active))
            if method in {"fetchone", "fetchmany", "fetchall", "next"}
            else None,
        )

    def execute(self, sql: str, parameters: Any = None, **kwargs: Any) -> Any:
        def live() -> Any:
            result = self._live().execute(sql, parameters, **kwargs)
            return None if self.connection._driver.name == "psycopg" else result

        result = self._call(
            "execute", {"sql": sql, "parameters": parameters, "options": kwargs}, live
        )
        return self if self.connection._driver.name == "psycopg" else result

    def executemany(self, sql: str, parameters: Any, **kwargs: Any) -> Any:
        if kwargs:
            unsupported("database_executemany_options_unsupported")
        return self._call(
            "executemany",
            {"sql": sql, "parameters": parameters, "options": kwargs},
            lambda: self._live().executemany(sql, parameters, **kwargs),
        )

    @property
    def description(self) -> Any:
        def live() -> Any:
            columns = self._live().description
            if columns is None:
                return None
            # psycopg.Column is a DBAPI sequence, not a serializable driver object.
            if self.connection._driver.name == "psycopg":
                return [tuple(column) for column in columns]
            return columns

        value = self._call("description", (), live)
        if self.connection._driver.name == "psycopg" and value is not None:
            return [Column(*column) for column in value]
        return value

    @property
    def statusmessage(self) -> Any:
        return self._call("statusmessage", (), lambda: self._live().statusmessage)

    @property
    def closed(self) -> Any:
        return self._call("closed", (), lambda: self._live().closed)

    def __enter__(self) -> "Cursor":
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


def engine_options(kwargs: dict[str, Any]) -> None:
    if {"creator", "module", "pool", "poolclass", "plugins"}.intersection(kwargs):
        raise ValueError("custom pools, creators, DBAPI modules and plugins are unsupported")
