# Database capture and replay

[Documentation home](index.md) · [Quick start](getting-started.md)

Install the SQLAlchemy integration with `pip install 'jaysoft-rewind[sqlalchemy]'`.
`rewind.adapters.dbapi.RecordingSQLite` needs only Python's built-in `sqlite3`.
The supported database boundary is synchronous SQLite, directly or through
SQLAlchemy 2.x Core and ORM. Other SQLAlchemy dialects and async drivers are not
supported by this adapter.

## SQLAlchemy

Create an engine inside the captured entry point, using Rewind's engine factory:

```python
from sqlalchemy import text
from rewind.adapters.sqlalchemy import create_engine

async def operation():
    engine = create_engine("orders.sqlite", dependency="orders")
    try:
        with engine.begin() as connection:
            row = connection.execute(
                text("select status from orders where id = :id"), {"id": 7}
            ).one()
            return tuple(row)
    finally:
        engine.dispose()
```

Run this with `await rewind.run(operation)` and replay with
`await rewind.replay(snapshot, operation)`. SQLAlchemy executes its normal result
processors and ORM logic against replayed DBAPI observations. `Decimal`, date,
and bytes application results retain their supported codec types. Convert
SQLAlchemy `Row` objects to tuples or dictionaries before returning them as an
entry-point result; arbitrary Python objects are intentionally not serialized.

The factory uses `NullPool`: each checkout has a separate connection. For an
in-memory SQLite database, keep one connection checked out for the entire unit
of work and bind ORM sessions to that connection. Multiple checkouts each get a
new, empty in-memory database. File-backed SQLite works across checkouts.
Custom pools, creators, DBAPI modules, and plugins are rejected because they
could bypass the instrumented boundary. Constructing and closing the engine
inside each entry point also keeps SQLAlchemy dialect initialization consistent
between capture and replay. Connections created outside capture make the trace
ineligible; replay will never reuse such a live connection.

## Direct SQLite

```python
from rewind.adapters.dbapi import RecordingSQLite

async def operation():
    connection = RecordingSQLite(dependency="orders").connect("orders.sqlite")
    try:
        cursor = connection.execute("select id, status from orders where id > ?", (7,))
        first = cursor.fetchone()
        cursor.arraysize = 2
        second = cursor.fetchmany()
        cursor.close()
        return first, second
    finally:
        connection.close()
```

The adapter records each application-visible call in order: connection creation,
cursor creation, `execute`, `executemany`, fetch operations, iterator advancement,
cursor metadata, transaction boundaries, and closing. It does not fetch rows at
`execute` time or drain an iterator behind the application's back. Closing after
one row records only that row. Replaying a different fetch method or batch size,
changed SQL or parameters, a different cursor or connection, or a changed
commit/rollback order diverges before any live database operation.

Supported cursor observations include `description`, `rowcount`, `lastrowid`,
`arraysize`, `fetchone`, `fetchmany`, `fetchall`, and iteration, including
exhaustion. Connection observations include `in_transaction`, `isolation_level`,
commit, rollback, close, and SQLite transaction context managers. Database
exceptions reproduce the allowlisted SQLite exception class, arguments (when
permitted), and SQLite error code/name. Blob bind parameters expressed as
`memoryview` or `bytearray` normalize to bounded byte values for trace matching;
the live driver still receives the original objects.

## Privacy and supported limits

Default capture policy excludes SQL and values, so such recordings are
ineligible for replay. `CapturePolicy.synthetic()` opts into fixture data.
SQL literals, positional parameters, and result tuples can contain secrets that
cannot be recognized by column name. Do not treat synthetic mode as a production
redaction guarantee. Sensitive keys in named parameter dictionaries are scrubbed
by the normal capture policy, making the trace ineligible.

The database filename is not stored. A dependency name identifies the logical
database: use distinct names for distinct databases. Keep the same logical names
in the replay application. Other connection options, SQL, parameters, call order,
and observed results are included in strict matching.

Capture uses the same per-value, artifact, interaction, and owner-task limits as
other adapters. Capture failures and limits preserve the application's real
result or driver exception and mark the trace ineligible. Generator parameters
for `executemany` are passed to the driver once and are not copied or consumed by
capture; the resulting trace is ineligible. Custom row factories, adapters that
return unsupported objects, extension methods, arbitrary Python SQLite UDFs,
raw handles, and cross-execution connections are also outside replay scope.
SQLAlchemy's standard `regexp` and `floor` initialization is supported.

Replay never calls `sqlite3.connect` or any live cursor/connection method. It is
still an adapter boundary, not a process sandbox: application code bypassing
this adapter needs the external offline runner's isolation.

## Executable evidence

Run `python -m examples.database_failure --store .rewind/database-demo`, then
replay the printed file using the trusted factory:

```sh
rewind replay RECORDING.rewind.json --app examples.database_failure:replay_target
```

`tests/test_database.py` exercises real SQLite and SQLAlchemy Core/ORM capture,
then replaces `sqlite3.connect` with a failing function during replay. It covers
partial consumption without prefetch, strict divergence, blob/date/decimal
values, SQLite exceptions, transaction rollback, privacy, limits, and unsupported
settings. No database service is needed.

API references: [SQLAlchemy engine creator](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.creator),
[SQLite dialect](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html),
and [Python sqlite3](https://docs.python.org/3/library/sqlite3.html).
