"""Explicit synchronous psycopg3 and SQLAlchemy PostgreSQL capture/replay."""

from typing import Any, cast

from ..replay import ReplaySession
from .relational import Connection as BaseConnection
from .relational import Driver, engine_options, unsupported

_DIAGNOSTICS = {
    "severity": "SEVERITY",
    "severity_nonlocalized": "SEVERITY_NONLOCALIZED",
    "sqlstate": "SQLSTATE",
    "message_primary": "MESSAGE_PRIMARY",
    "message_detail": "MESSAGE_DETAIL",
    "message_hint": "MESSAGE_HINT",
    "statement_position": "STATEMENT_POSITION",
    "internal_position": "INTERNAL_POSITION",
    "internal_query": "INTERNAL_QUERY",
    "context": "CONTEXT",
    "schema_name": "SCHEMA_NAME",
    "table_name": "TABLE_NAME",
    "column_name": "COLUMN_NAME",
    "datatype_name": "DATATYPE_NAME",
    "constraint_name": "CONSTRAINT_NAME",
    "source_file": "SOURCE_FILE",
    "source_line": "SOURCE_LINE",
    "source_function": "SOURCE_FUNCTION",
}


class PostgresDriver(Driver):
    def error_data(self, error: BaseException) -> dict[str, Any]:
        data = super().error_data(error)
        if data["type"] in self.errors:
            data["diagnostics"] = {
                field: getattr(cast(Any, error).diag, field) for field in _DIAGNOSTICS
            }
        return data

    def restore_error(self, data: Any, active: ReplaySession) -> Exception:
        from psycopg.pq import DiagnosticField

        if type(data) is not dict or set(data) != {"type", "args", "diagnostics"}:
            active.fail("invalid PostgreSQL error envelope")
        # Validate class and positional arguments against the fixed driver map.
        super().restore_error({"type": data["type"], "args": data["args"]}, active)
        diagnostics = data["diagnostics"]
        if (
            type(diagnostics) is not dict
            or set(diagnostics) != set(_DIAGNOSTICS)
            or any(value is not None and type(value) is not str for value in diagnostics.values())
        ):
            active.fail("invalid PostgreSQL diagnostics")
        info = {
            getattr(DiagnosticField, _DIAGNOSTICS[key]): value.encode("utf-8")
            for key, value in diagnostics.items()
            if value is not None
        }
        return cast(Any, self.errors[data["type"]])(*data["args"], info=info)


class ConnectionInfo:
    def __init__(self, connection: "Connection") -> None:
        self._connection = connection

    def _get(self, name: str) -> Any:
        return self._connection._call(
            "info." + name, (), lambda: getattr(self._connection._live().info, name)
        )

    @property
    def transaction_status(self) -> Any:
        from psycopg.pq import TransactionStatus

        value = self._connection._call(
            "info.transaction_status",
            (),
            lambda: int(self._connection._live().info.transaction_status),
        )
        return TransactionStatus(value)

    @property
    def server_version(self) -> Any:
        return self._get("server_version")

    @property
    def encoding(self) -> Any:
        return self._get("encoding")

    def parameter_status(self, name: str) -> Any:
        # These values describe parsing/runtime semantics, not connection secrets.
        if name not in {
            "client_encoding",
            "server_version",
            "standard_conforming_strings",
            "TimeZone",
        }:
            unsupported("postgres_parameter_status_unsupported")
        return self._connection._call(
            "info.parameter_status",
            name,
            lambda: self._connection._live().info.parameter_status(name),
        )

    def __getattr__(self, name: str) -> Any:
        unsupported("postgres_connection_info_unsupported")
        return getattr(self._connection._live().info, name)


class Connection(BaseConnection):
    @property
    def info(self) -> ConnectionInfo:
        return ConnectionInfo(self)

    @property
    def autocommit(self) -> Any:
        return self._call("autocommit", (), lambda: self._live().autocommit)

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self._call("set_autocommit", value, lambda: setattr(self._live(), "autocommit", value))

    @property
    def isolation_level(self) -> Any:
        from psycopg import IsolationLevel

        value = self._call(
            "isolation_level",
            (),
            lambda: (
                int(self._live().isolation_level)
                if self._live().isolation_level is not None
                else None
            ),
        )
        return IsolationLevel(value) if value is not None else None

    @isolation_level.setter
    def isolation_level(self, value: Any) -> None:
        normalized = int(value) if value is not None else None
        self._call(
            "set_isolation_level",
            normalized,
            lambda: setattr(self._live(), "isolation_level", value),
        )

    @property
    def closed(self) -> Any:
        return self._call("closed", (), lambda: self._live().closed)

    @property
    def broken(self) -> Any:
        return self._call("broken", (), lambda: self._live().broken)

    @property
    def read_only(self) -> Any:
        return self._call("read_only", (), lambda: self._live().read_only)

    @read_only.setter
    def read_only(self, value: Any) -> None:
        self._call("set_read_only", value, lambda: setattr(self._live(), "read_only", value))

    @property
    def deferrable(self) -> Any:
        return self._call("deferrable", (), lambda: self._live().deferrable)

    @deferrable.setter
    def deferrable(self, value: Any) -> None:
        self._call("set_deferrable", value, lambda: setattr(self._live(), "deferrable", value))

    def add_notice_handler(self, callback: Any) -> None:
        if (
            getattr(callback, "__module__", "") != "sqlalchemy.dialects.postgresql.psycopg"
            or getattr(callback, "__name__", "") != "_log_notices"
        ):
            unsupported("postgres_notice_handler_unsupported")
        self._call(
            "add_notice_handler",
            "sqlalchemy.logger",
            lambda: self._live().add_notice_handler(callback),
        )


class RecordingPostgres:
    def __init__(self, *, dependency: str = "postgres") -> None:
        import psycopg

        names = (
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
        states = (
            "UniqueViolation",
            "NotNullViolation",
            "ForeignKeyViolation",
            "CheckViolation",
            "UndefinedTable",
            "UndefinedColumn",
            "SyntaxError",
            "DivisionByZero",
            "NumericValueOutOfRange",
            "InvalidTextRepresentation",
            "InFailedSqlTransaction",
            "SerializationFailure",
            "DeadlockDetected",
            "QueryCanceled",
        )
        errors = [getattr(psycopg, name) for name in names]
        errors.extend(getattr(psycopg.errors, name) for name in states)
        self._driver = PostgresDriver(
            "psycopg", {f"{cls.__module__}.{cls.__qualname__}": cls for cls in errors}
        )
        self.dependency = dependency

    def connect(self, conninfo: str = "", **kwargs: Any) -> Connection:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        if {"row_factory", "cursor_factory", "context"}.intersection(kwargs):
            unsupported("custom_postgres_connection_unsupported")
        # libpq parses the DSN but only the explicit non-secret option allowlist
        # enters the artifact. Passwords/users/hosts/database names stay out.
        config = conninfo_to_dict(conninfo)
        config.update(kwargs)
        safe = {"autocommit", "prepare_threshold", "client_encoding"}
        options = {key: value for key, value in config.items() if key in safe}
        return self._driver.connect(
            self.dependency, options, lambda: psycopg.connect(conninfo, **kwargs), Connection
        )


def create_engine(
    conninfo: str = "",
    *,
    dependency: str = "postgres",
    connect_args: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    from sqlalchemy import create_engine as sa_create_engine
    from sqlalchemy.pool import NullPool

    engine_options(kwargs)
    if kwargs.get("use_native_hstore", False):
        raise ValueError("native hstore registration is outside the replay boundary")
    kwargs["use_native_hstore"] = False
    connector = RecordingPostgres(dependency=dependency)
    options = dict(connect_args or {})
    return sa_create_engine(
        "postgresql+psycopg://",
        creator=lambda: connector.connect(conninfo, **options),
        poolclass=NullPool,
        **kwargs,
    )
