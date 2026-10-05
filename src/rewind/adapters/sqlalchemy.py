"""SQLAlchemy 2.x synchronous SQLite engine with an explicit replay boundary."""

from typing import Any

from .dbapi import RecordingSQLite


def create_engine(
    database: str = ":memory:",
    *,
    dependency: str = "database",
    connect_args: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Create a SQLite engine; construct it inside the recorded entry point.

    NullPool ensures each checkout crosses the connection boundary. Alternate
    pools, creators, drivers and DBAPI modules would bypass the boundary and are
    rejected. The database filename is supplied only to the live connector.
    """
    from sqlalchemy import create_engine as sa_create_engine
    from sqlalchemy.pool import NullPool

    forbidden = {"creator", "module", "pool", "poolclass", "plugins"}.intersection(kwargs)
    if forbidden:
        raise ValueError("custom pooling, drivers and plugins are unsupported")
    connector = RecordingSQLite(dependency=dependency)
    options = dict(connect_args or {})
    return sa_create_engine(
        "sqlite+pysqlite://",
        creator=lambda: connector.connect(database, **options),
        poolclass=NullPool,
        **kwargs,
    )
