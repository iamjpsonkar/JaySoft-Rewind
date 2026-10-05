"""Explicit synchronous PyMySQL and SQLAlchemy MySQL capture/replay."""

from importlib import import_module
from typing import Any

from .relational import Connection as BaseConnection
from .relational import Driver, engine_options, unsupported


class Connection(BaseConnection):
    def autocommit(self, value: bool) -> Any:
        return self._call("autocommit", value, lambda: self._live().autocommit(value))

    def get_autocommit(self) -> Any:
        return self._call("get_autocommit", (), lambda: self._live().get_autocommit())

    def character_set_name(self) -> Any:
        return self._call("character_set_name", (), lambda: self._live().character_set_name())

    def get_server_info(self) -> Any:
        return self._call("get_server_info", (), lambda: self._live().get_server_info())

    def begin(self) -> Any:
        return self._call("begin", (), lambda: self._live().begin())

    def ping(self, reconnect: bool = True) -> Any:
        return self._call("ping", reconnect, lambda: self._live().ping(reconnect))

    @property
    def open(self) -> Any:
        return self._call("open", (), lambda: self._live().open)


class RecordingMySQL:
    def __init__(self, *, dependency: str = "mysql") -> None:
        pymysql = import_module("pymysql")

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
        errors = [getattr(pymysql, name) for name in names]
        self._driver = Driver(
            "pymysql", {f"{cls.__module__}.{cls.__qualname__}": cls for cls in errors}
        )
        self.dependency = dependency

    def connect(self, **kwargs: Any) -> Connection:
        pymysql = import_module("pymysql")

        safe = {"charset", "collation", "autocommit", "use_unicode", "binary_prefix", "sql_mode"}
        if {"cursorclass", "conv", "init_command", "auth_plugin_map", "local_infile"}.intersection(
            kwargs
        ):
            unsupported("custom_mysql_connection_unsupported")
        options = {key: value for key, value in kwargs.items() if key in safe}
        return self._driver.connect(
            self.dependency, options, lambda: pymysql.connect(**kwargs), Connection
        )


def create_engine(
    *, dependency: str = "mysql", connect_args: dict[str, Any] | None = None, **kwargs: Any
) -> Any:
    from sqlalchemy import create_engine as sa_create_engine
    from sqlalchemy.pool import NullPool

    engine_options(kwargs)
    connector = RecordingMySQL(dependency=dependency)
    options = dict(connect_args or {})
    return sa_create_engine(
        "mysql+pymysql://",
        creator=lambda: connector.connect(**options),
        poolclass=NullPool,
        **kwargs,
    )
