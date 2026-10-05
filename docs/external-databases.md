# PostgreSQL and MySQL replay

The explicit synchronous adapters support psycopg 3 and PyMySQL 1.x, directly or
through SQLAlchemy 2.x. Install `jaysoft-rewind[postgres]` or
`jaysoft-rewind[mysql]`; the core package does not require either driver.

Create connections and engines inside the captured entry point. Each dependency
name identifies one logical database, independently of its hostname or credentials.

```python
from rewind.adapters.postgres import RecordingPostgres
from rewind.adapters.mysql import RecordingMySQL

# Inside a function passed to rewind.run_sync(...):
with RecordingPostgres(dependency="orders").connect(
    "host=localhost dbname=fixture user=fixture password=fixture"
) as connection:
    with connection.cursor() as cursor:
        cursor.execute("select amount from orders where id = %s", (7,))
        row = cursor.fetchone()

# PyMySQL uses keyword connection arguments and execute returns affected rows.
with RecordingMySQL(dependency="catalog").connect(
    host="localhost", database="fixture", user="fixture", password="fixture"
) as connection:
    with connection.cursor() as cursor:
        affected = cursor.execute("select name from catalog where id = %s", (7,))
        row = cursor.fetchone()
```

The connection context manager retains each driver's semantics: psycopg commits
or rolls back and closes on exit; PyMySQL closes on exit and applications must
commit explicitly. Rewind delegates actual capture behavior to the driver.

## SQLAlchemy

Use the driver-specific factory. Engines use `NullPool`; alternate creators,
pools, DBAPI modules and plugins are rejected because they bypass the boundary.

```python
from sqlalchemy import text
from rewind.adapters.postgres import create_engine

# Construct and dispose this engine within the captured entry point.
engine = create_engine(
    "host=localhost dbname=fixture user=fixture password=fixture",
    dependency="orders",
)
try:
    with engine.begin() as connection:
        row = connection.execute(text("select 7 as item_id")).one()
        result = tuple(row)
finally:
    engine.dispose()
```

MySQL uses `rewind.adapters.mysql.create_engine(connect_args={...})`. Core and
ORM sessions run their usual SQL and result processors. PostgreSQL ORM generated
IDs use normal `RETURNING`; MySQL generated IDs come from `lastrowid`. SQLAlchemy's
known immutable parameter mappings and quoted parameter names are normalized
inside the bounded capture path. Arbitrary mappings and iterators are not eagerly
consumed for recording.

The tested API family is **SQLAlchemy 2.x**. SQLAlchemy 1.3, psycopg2, asyncpg,
mysqlclient, asynchronous database engines, and executor-thread database calls
are not covered. Validate the actual library versions and execution model
before integrating these adapters.

## Recorded behavior

Each call is a `db.call` observation containing a fixed driver name, logical
dependency, connection/cursor identity, operation, arguments, and bounded result
or allowlisted exception. Supported behavior includes:

- `execute`, `executemany`, `fetchone`, `fetchmany`, `fetchall`, partial iteration,
  cursor close, and cursor/connection context managers.
- `description`, `rowcount`, `arraysize`, and MySQL `lastrowid`. PostgreSQL
  descriptions preserve the seven DBAPI fields and their named attributes using
  portable column descriptors; native driver class identity is not preserved.
- Commit and rollback, PostgreSQL autocommit/isolation/read-only/deferrable
  properties, and MySQL begin/autocommit/ping. Replayed ping never reconnects.
- PostgreSQL transaction status, server version, encoding and selected parameter
  status observations; MySQL charset, server info, autocommit and open status.
- Standard driver exception classes, MySQL numeric error codes, and explicitly
  allowlisted PostgreSQL SQLSTATE subclasses and diagnostic fields. Unsupported
  exception subclasses make a capture ineligible rather than loading a class
  from artifact content.

PostgreSQL SQLSTATE subclasses currently include unique/not-null/foreign-key/check
violations, undefined table/column, syntax/division/numeric/text conversion errors,
failed transaction, serialization failure, deadlock, and query cancellation.
Decimal, date, supported datetime, bytes, tuples, lists and other supported codec
values retain their types. PostgreSQL timestamptz values using ZoneInfo, specialized PostgreSQL values, and
MySQL TIME/timedelta need separate codec support and make a recording ineligible when unsupported.

Capture does not fetch ahead. Fetch order, batch size, SQL, parameters, connection
identity and commit/rollback order are matched strictly. Replay returns stored
observations without calling driver connect or live connection/cursor methods.
Extra, missing or changed operations diverge; there is no live fallback.

## Privacy and unsupported boundaries

Passwords, DSNs, usernames, hostnames, database names, TLS keys and authentication
configuration are not recorded as connection inputs. Only explicit non-secret
behavior options are retained: PostgreSQL autocommit/preparation threshold/client
encoding; MySQL charset/collation/autocommit/unicode/binary-prefix/SQL mode. Use
distinct dependency names for distinct logical databases. Endpoint changes are
not part of trace matching.

Defaults exclude values and therefore produce ineligible diagnostic captures.
`CapturePolicy.synthetic()` is for fixtures. Configured sensitive SQL identifiers
cause the SQL and bindings to be excluded; sensitive result-column names are
redacted in detached captured rows. Application rows are returned unchanged.
Exception details require the exception-argument opt-in; adapter connection-error
details are always excluded because they can echo credentials. An unhandled
application exception is also subject to the entry-point exception policy, so
synthetic mode is not a guarantee against arbitrary secret-bearing error text.

Native hstore registration, custom row/cursor factories, COPY, server-side
cursors, custom adaptation contexts, PostgreSQL pipeline mode, stored procedures,
multiple result sets, raw connection handles, arbitrary notices/callbacks, and
cross-execution or cross-thread connections are outside this contract. Unsupported
calls preserve ordinary live application behavior and mark capture ineligible;
replay rejects them. SQLAlchemy's built-in PostgreSQL notice logger is supported.

The default fresh-process guard prepares the standard-library ctypes interpreter
handle before installing its audit hook. psycopg's binary implementation can then
import its types; later `ctypes.CDLL`/`PyDLL` loads remain blocked. This is not a
native-code or OS sandbox. For externally enforced network isolation, run replay
inside a network-disabled container as well.

## Real conformance evidence

On 2026-10-05 the conformance suite ran against disposable PostgreSQL
`postgres:16-alpine` and MySQL `mysql:8.0.31` containers, with psycopg **3.3.6**,
PyMySQL **1.2.3**, SQLAlchemy **2.1.3**, and Python **3.12.13**. Tests exercised real
DBAPI and SQLAlchemy Core/ORM capture, generated IDs, transaction behavior,
metadata, partial consumption, typed values, errors, privacy, and bounded capture.
Replay then replaced the real driver connection factory with a failing function;
both drivers also passed a fresh guarded worker. These results do not establish
MySQL 8.4, SQLAlchemy 1.x, every driver version, or production readiness.

To reproduce against disposable local servers, expose PostgreSQL at localhost
55432 and MySQL at localhost 53306, create database `rewind`, and use synthetic
password `rewind-fixture` with users `postgres` and `root` respectively. Ports can
be overridden with `REWIND_POSTGRES_PORT` / `REWIND_MYSQL_PORT`.

```sh
REWIND_RELATIONAL_CONFORMANCE=1 python -m pytest tests/test_postgres.py tests/test_mysql.py -q
```

Without this explicit switch, service tests are skipped and local safety/configuration
tests still run. CI should enable it with disposable database services.

For a runnable synthetic failure from the repository checkout, configure
`REWIND_POSTGRES_DSN` or `REWIND_MYSQL_CONFIG` (a JSON connection-argument object):

```sh
python -m examples.external_database_failure --driver postgres
rewind replay RECORDING.rewind.json --app examples.external_database_failure:postgres_target
# MySQL: --driver mysql and --app examples.external_database_failure:mysql_target
```

References: [psycopg cursors](https://www.psycopg.org/psycopg3/docs/api/cursors.html),
[psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[PyMySQL connection API](https://pymysql.readthedocs.io/en/latest/modules/connections.html),
[SQLAlchemy psycopg dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg).
