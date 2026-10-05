import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Column, Date, Integer, LargeBinary, MetaData, Numeric, Table, select, text
from sqlalchemy.orm import Session

from rewind import CapturePolicy, Limits
from rewind.adapters.dbapi import RecordingSQLite
from rewind.adapters.sqlalchemy import create_engine


def saved(recorder):
    return recorder.store.load(recorder.store.ids()[0])


def no_database(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("replay accessed the live SQLite driver")

    monkeypatch.setattr(sqlite3, "connect", blocked)


async def test_fetch_observations_transactions_and_metadata(recorder, monkeypatch):
    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        cursor = conn.cursor()
        assert cursor.execute("create table item(id integer primary key, value blob)") is cursor
        cursor.executemany("insert into item(value) values (?)", [(b"a",), (b"b",), (b"c",)])
        assert cursor.rowcount == 3
        conn.commit()
        assert not conn.in_transaction
        cursor.execute("insert into item(value) values (?)", (b"d",))
        inserted = cursor.lastrowid
        assert conn.in_transaction
        conn.rollback()
        cursor.execute("select id, value from item order by id")
        description = cursor.description
        cursor.arraysize = 1
        assert cursor.arraysize == 1
        first = cursor.fetchone()
        middle = cursor.fetchmany()
        last = cursor.fetchall()
        assert cursor.fetchone() is None
        cursor.close()
        conn.close()
        return inserted, description, first, middle, last

    result = await recorder.run(operation)
    assert result[0] == 4
    assert result[2:] == ((1, b"a"), [(2, b"b")], [(3, b"c")])
    snapshot = saved(recorder)
    assert snapshot.complete
    no_database(monkeypatch)
    assert (await recorder.replay(snapshot, operation)).reproduced


async def test_partial_iteration_does_not_prefetch(recorder, monkeypatch):
    live_observations = []

    class CountingCursor(sqlite3.Cursor):
        def fetchall(self):
            raise AssertionError("adapter must not prefetch")

        def __next__(self):
            live_observations.append("next")
            return super().__next__()

    class CountingConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=CountingCursor)

    # Inject only the live driver, leaving captured options data-only.
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: real_connect(*a, **kw, factory=CountingConnection)
    )

    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        cursor = conn.execute("select 1 union all select 2 union all select 3")
        first = next(iter(cursor))
        cursor.close()
        conn.close()
        return first

    assert await recorder.run(operation) == (1,)
    assert live_observations == ["next"]
    no_database(monkeypatch)
    assert (await recorder.replay(saved(recorder), operation)).reproduced
    assert live_observations == ["next"]


async def test_iteration_exhaustion_and_multiple_connections(recorder, monkeypatch):
    async def operation():
        left = RecordingSQLite().connect(":memory:")
        right = RecordingSQLite().connect(":memory:")
        a = left.execute("select 1 union all select 2")
        b = right.execute("select 3")
        result = next(a), list(b), list(a)
        left.close()
        right.close()
        return result

    assert await recorder.run(operation) == ((1,), [(3,)], [(2,)])
    snapshot = saved(recorder)
    no_database(monkeypatch)
    assert (await recorder.replay(snapshot, operation)).reproduced


@pytest.mark.parametrize("change", ["sql", "params", "fetch", "commit", "dependency"])
async def test_strict_mismatch_fails_before_database(recorder, monkeypatch, change):
    changed = False

    async def operation():
        conn = RecordingSQLite(
            dependency="other" if changed and change == "dependency" else "db"
        ).connect(":memory:")
        cursor = conn.execute(
            "select ? + 1" if changed and change == "sql" else "select ?",
            (2 if changed and change == "params" else 1,),
        )
        result = cursor.fetchall() if changed and change == "fetch" else cursor.fetchone()
        conn.rollback() if changed and change == "commit" else conn.commit()
        conn.close()
        return result

    await recorder.run(operation)
    changed = True
    no_database(monkeypatch)
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"


async def test_database_exceptions_preserve_type_and_attributes(recorder, monkeypatch):
    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        conn.execute("create table item(id integer primary key)")
        conn.execute("insert into item values (1)")
        try:
            conn.execute("insert into item values (1)")
        except sqlite3.IntegrityError as error:
            result = (
                type(error).__name__,
                error.args,
                error.sqlite_errorcode,
                error.sqlite_errorname,
            )
        conn.rollback()
        conn.close()
        return result

    assert (await recorder.run(operation))[0] == "IntegrityError"
    no_database(monkeypatch)
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_context_manager_rollback(recorder, monkeypatch):
    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        conn.execute("create table item(value integer)")
        try:
            with conn:
                conn.execute("insert into item values (1)")
                raise ValueError("rollback")
        except ValueError:
            pass
        result = conn.execute("select count(*) from item").fetchone()
        conn.close()
        return result

    assert await recorder.run(operation) == (0,)
    no_database(monkeypatch)
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_generator_parameters_not_consumed_twice(recorder):
    observations = []

    def parameters():
        for value in range(3):
            observations.append(value)
            yield (value,)

    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        conn.execute("create table item(value integer)")
        conn.executemany("insert into item values (?)", parameters())
        result = conn.execute("select count(*) from item").fetchone()
        conn.close()
        return result

    assert await recorder.run(operation) == (3,)
    assert observations == [0, 1, 2]
    assert not saved(recorder).complete


async def test_defaults_exclude_sql_and_values(recorder):
    recorder.policy = CapturePolicy()

    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        result = conn.execute("select 'private_sql_literal'").fetchone()
        conn.close()
        return result

    assert await recorder.run(operation) == ("private_sql_literal",)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"private_sql_literal" not in snapshot.raw


async def test_capture_limits_do_not_replace_application_behavior(recorder):
    recorder.limits = Limits(interactions=3)

    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        result = conn.execute("select 42").fetchall()
        conn.close()
        return result

    assert await recorder.run(operation) == [(42,)]
    assert not saved(recorder).complete


async def test_unsupported_function_marks_recording_ineligible(recorder):
    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        conn.create_function("double", 1, lambda value: value * 2)
        result = conn.execute("select double(4)").fetchone()
        conn.close()
        return result

    assert await recorder.run(operation) == (8,)
    assert "database_function_unsupported" in saved(recorder).data["capture"]["ineligible_reasons"]


async def test_connection_outside_capture_ineligible(recorder):
    conn = RecordingSQLite().connect(":memory:")

    async def operation():
        return conn.execute("select 42").fetchone()

    assert await recorder.run(operation) == (42,)
    conn.close()
    assert not saved(recorder).complete


async def test_sqlalchemy_core_and_orm_transactions(recorder, monkeypatch):
    async def operation():
        engine = create_engine()
        metadata = MetaData()
        items = Table(
            "items",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(10, 2)),
            Column("day", Date),
            Column("content", LargeBinary),
        )
        # Keep a single checkout for the in-memory database's whole lifetime.
        with engine.connect() as connection:
            metadata.create_all(connection)
            with Session(bind=connection) as session:
                session.execute(
                    items.insert().values(
                        amount=Decimal("12.50"), day=date(2026, 1, 2), content=b"value"
                    )
                )
                session.commit()
                result = tuple(session.execute(select(items)).one())
                assert session.execute(text("select count(*) from items")).scalar() == 1
        engine.dispose()
        return result

    assert await recorder.run(operation) == (1, Decimal("12.50"), date(2026, 1, 2), b"value")
    snapshot = saved(recorder)
    assert snapshot.complete, snapshot.data["capture"]
    no_database(monkeypatch)
    report = await recorder.replay(snapshot, operation)
    assert report.reproduced, report


@pytest.mark.parametrize("option", ["creator", "module", "pool", "poolclass", "plugins"])
def test_sqlalchemy_rejects_boundary_bypass(option):
    with pytest.raises(ValueError, match="unsupported"):
        create_engine(**{option: None})


async def test_sqlite_row_factory_setting_keeps_application_behavior(recorder):
    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute("select 42 as answer").fetchone()
        conn.close()
        return row["answer"]

    assert await recorder.run(operation) == 42
    assert not saved(recorder).complete


async def test_closed_cursor_driver_error_is_replayed(recorder, monkeypatch):
    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        cursor = conn.cursor()
        cursor.close()
        try:
            cursor.fetchone()
        except sqlite3.ProgrammingError as error:
            result = error.args
        conn.close()
        return result

    assert await recorder.run(operation)
    no_database(monkeypatch)
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_connection_order_cannot_be_swapped(recorder, monkeypatch):
    swapped = False

    async def operation():
        left = RecordingSQLite().connect(":memory:")
        right = RecordingSQLite().connect(":memory:")
        first, second = (right, left) if swapped else (left, right)
        result = first.execute("select 1").fetchone(), second.execute("select 1").fetchone()
        left.close()
        right.close()
        return result

    await recorder.run(operation)
    swapped = True
    no_database(monkeypatch)
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"


async def test_capture_exception_does_not_replace_query_result(recorder, monkeypatch):
    from rewind.recorder import Recorder

    def broken(*args, **kwargs):
        raise RuntimeError("capture backend unavailable")

    monkeypatch.setattr(Recorder, "begin", broken)

    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        result = conn.execute("select 42").fetchone()
        conn.close()
        return result

    assert await recorder.run(operation) == (42,)
    assert not saved(recorder).complete


async def test_exception_arguments_respect_privacy_setting(recorder):
    recorder.policy = CapturePolicy(capture_values=True)

    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        try:
            conn.execute("select unavailable_secret_column")
        except sqlite3.OperationalError:
            pass
        conn.close()

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"no such column" not in snapshot.raw


async def test_malformed_database_result_diverges(recorder, monkeypatch):
    from rewind.codecs import encode
    from rewind.snapshot import Snapshot

    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        result = conn.execute("select 42").fetchone()
        conn.close()
        return result

    await recorder.run(operation)
    data = saved(recorder).data
    data["interactions"][0]["outcome"]["value"] = encode([], recorder.limits)
    snapshot = Snapshot.from_dict(data, recorder.limits)
    no_database(monkeypatch)
    assert (await recorder.replay(snapshot, operation)).status == "diverged"


async def test_isolation_level_and_cursor_connection_identity(recorder, monkeypatch):
    async def operation():
        conn = RecordingSQLite().connect(":memory:")
        conn.isolation_level = None
        assert conn.isolation_level is None
        cursor = conn.execute("select 42")
        assert cursor.connection is conn
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result

    assert await recorder.run(operation) == (42,)
    no_database(monkeypatch)
    assert (await recorder.replay(saved(recorder), operation)).reproduced


def test_cursor_connection_is_readonly():
    conn = RecordingSQLite().connect(":memory:")
    cursor = conn.cursor()
    with pytest.raises(AttributeError):
        cursor.connection = conn
    conn.close()
