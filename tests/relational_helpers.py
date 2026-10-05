"""Real disposable server conformance shared by the two relational adapters."""

import os
from decimal import Decimal

import pytest
from sqlalchemy import text


def require_server():
    if os.environ.get("REWIND_RELATIONAL_CONFORMANCE") != "1":
        pytest.skip("enable REWIND_RELATIONAL_CONFORMANCE for disposable database tests")


def saved(recorder):
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.complete, snapshot.data["capture"]
    return snapshot


def blocked(*args, **kwargs):
    raise AssertionError("replay attempted a live database connection")


def exercise(connector, postgres):
    connection = connector()
    cursor = connection.cursor()
    identity = "integer generated always as identity" if postgres else "integer auto_increment"
    cursor.execute(f"create temporary table rewind_items(id {identity} primary key, value integer)")
    cursor.executemany("insert into rewind_items(value) values (%s)", [(10,), (20,), (30,)])
    assert cursor.rowcount == 3
    connection.commit()
    cursor.execute("insert into rewind_items(value) values (%s)", (40,))
    last_id = None if postgres else cursor.lastrowid
    connection.rollback()
    cursor.execute("select id, value from rewind_items order by id")
    description = [tuple(column) for column in cursor.description]
    cursor.arraysize = 1
    assert cursor.arraysize == 1
    first = cursor.fetchone()
    middle = cursor.fetchmany()
    last = list(cursor)
    assert cursor.fetchone() is None
    cursor.close()
    connection.close()
    return description, first, middle, last, last_id


def typed_values(connector, postgres):
    connection = connector()
    cursor = connection.cursor()
    if postgres:
        cursor.execute("select 12.50::numeric(10,2), '2026-10-05'::date, decode('6162','hex')")
    else:
        cursor.execute(
            "select cast(12.50 as decimal(10,2)), cast('2026-10-05' as date), unhex('6162')"
        )
    result = cursor.fetchone()
    assert result[0] == Decimal("12.50") and result[2] == b"ab"
    cursor.close()
    connection.close()
    return result


def sqlalchemy_operation(factory, postgres):
    engine = factory()
    try:
        with engine.connect() as connection:
            identity = (
                "integer generated always as identity" if postgres else "integer auto_increment"
            )
            connection.execute(
                text(
                    f"create temporary table rewind_items(id {identity} primary key, value integer)"
                )
            )
            connection.execute(
                text("insert into rewind_items(value) values (:value)"), {"value": 42}
            )
            connection.commit()
            result = tuple(connection.execute(text("select id,value from rewind_items")).one())
            connection.execute(text("insert into rewind_items(value) values (43)"))
            connection.rollback()
            assert connection.execute(text("select count(*) from rewind_items")).scalar() == 1
            return result
    finally:
        engine.dispose()


def orm_operation(factory):
    from sqlalchemy import Column, Integer, MetaData, String, Table, select
    from sqlalchemy.orm import Session

    engine = factory()
    table = Table(
        "rewind_orm",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("value", String(32)),
        prefixes=["TEMPORARY"],
    )
    try:
        with engine.connect() as connection:
            table.create(connection, checkfirst=False)
            with Session(bind=connection) as session:
                inserted = session.execute(
                    table.insert().values(value="fixture")
                ).inserted_primary_key
                session.commit()
                row = session.execute(select(table)).one()
                return tuple(inserted), tuple(row)
    finally:
        engine.dispose()


def private_rows(connector, postgres):
    connection = connector()
    cursor = connection.cursor()
    # The SQL has a sensitive column name; both the statement and results are
    # removed from the artifact while application data remains unchanged.
    cursor.execute("select %s as password", ("synthetic-private-value",))
    rows = cursor.fetchall()
    assert rows[0][0] == "synthetic-private-value"
    connection.close()
    return None


def worker_target(driver):
    from pathlib import Path

    from rewind import CapturePolicy, ReplayTarget, Rewind

    rewind = Rewind(
        application="tests",
        code_paths=[Path(__file__).with_name("conftest.py")],
        policy=CapturePolicy.synthetic(),
    )
    if driver == "postgres":
        import psycopg

        from .test_postgres import connector

        psycopg.connect = blocked
    else:
        import pymysql

        from .test_mysql import connector

        pymysql.connect = blocked
    return ReplayTarget(
        rewind, lambda: typed_values(connector, driver == "postgres"), kind="callable_sync"
    )


def postgres_target():
    return worker_target("postgres")


def mysql_target():
    return worker_target("mysql")
