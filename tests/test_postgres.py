import os

import pytest

from rewind.adapters.postgres import RecordingPostgres, create_engine

from .relational_helpers import (
    blocked,
    exercise,
    require_server,
    saved,
    sqlalchemy_operation,
    typed_values,
)

psycopg = pytest.importorskip("psycopg")


def options():
    return dict(
        host="127.0.0.1",
        port=int(os.environ.get("REWIND_POSTGRES_PORT", "55432")),
        user="postgres",
        password="rewind-fixture",
        dbname="rewind",
        connect_timeout=5,
    )


def connector():
    return RecordingPostgres().connect(**options())


def test_postgres_dbapi_real_server(recorder, monkeypatch):
    require_server()
    result = recorder.run_sync(lambda: exercise(connector, True))
    assert result[1:4] == ((1, 10), [(2, 20)], [(3, 30)])
    snapshot = saved(recorder)
    assert b"rewind-fixture" not in snapshot.raw
    monkeypatch.setattr(psycopg, "connect", blocked)
    assert recorder.replay_sync(snapshot, lambda: exercise(connector, True)).reproduced


def test_postgres_typed_results_real_server(recorder, monkeypatch):
    require_server()
    recorder.run_sync(lambda: typed_values(connector, True))
    monkeypatch.setattr(psycopg, "connect", blocked)
    assert recorder.replay_sync(saved(recorder), lambda: typed_values(connector, True)).reproduced


def test_postgres_sqlalchemy_real_server(recorder, monkeypatch):
    require_server()

    def factory():
        return create_engine(connect_args=options())

    def operation():
        return sqlalchemy_operation(factory, True)

    assert recorder.run_sync(operation) == (1, 42)
    snapshot = saved(recorder)
    monkeypatch.setattr(psycopg, "connect", blocked)
    report = recorder.replay_sync(snapshot, operation)
    assert report.reproduced, report


def test_postgres_error_and_transaction_state_real_server(recorder, monkeypatch):
    require_server()

    def operation():
        conn = connector()
        cursor = conn.cursor()
        cursor.execute("create temporary table rewind_unique(value integer primary key)")
        cursor.execute("insert into rewind_unique values (1)")
        conn.commit()
        try:
            cursor.execute("insert into rewind_unique values (1)")
        except psycopg.errors.UniqueViolation as error:
            result = (
                type(error).__name__,
                error.args,
                error.sqlstate,
                error.diag.constraint_name,
                error.diag.message_detail,
                int(conn.info.transaction_status),
            )
        conn.rollback()
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        conn.close()
        return result

    assert recorder.run_sync(operation)[2] == "23505"
    snapshot = saved(recorder)
    monkeypatch.setattr(psycopg, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).reproduced


def test_postgres_changed_query_fails_closed(recorder, monkeypatch):
    require_server()
    changed = False

    def operation():
        conn = connector()
        row = conn.execute("select %s", (2 if changed else 1,)).fetchone()
        conn.close()
        return row

    recorder.run_sync(operation)
    snapshot = saved(recorder)
    changed = True
    monkeypatch.setattr(psycopg, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).status == "diverged"


def test_postgres_password_not_saved_on_connection_failure(recorder, monkeypatch):
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("password=super-private")

    monkeypatch.setattr(psycopg, "connect", fail)
    with pytest.raises(psycopg.OperationalError):
        recorder.run_sync(lambda: RecordingPostgres().connect("password=super-private"))
    snapshot = recorder.store.load(recorder.store.ids()[0])
    # The application exception is also captured at the entrypoint; return it
    # from a handled call to inspect the adapter's exclusion independently.
    from rewind.codecs import dumps

    assert b"super-private" not in dumps(snapshot.data["interactions"])
    assert not snapshot.complete


def test_postgres_rejects_native_hstore():
    with pytest.raises(ValueError, match="hstore"):
        create_engine(use_native_hstore=True)


def test_postgres_orm_generated_identity_real_server(recorder, monkeypatch):
    require_server()
    from .relational_helpers import orm_operation

    def operation():
        return orm_operation(lambda: create_engine(connect_args=options()))

    assert recorder.run_sync(operation) == ((1,), (1, "fixture"))
    snapshot = saved(recorder)
    monkeypatch.setattr(psycopg, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).reproduced


def test_postgres_redacts_sensitive_columns_real_server(recorder):
    require_server()
    from .relational_helpers import private_rows

    recorder.run_sync(lambda: private_rows(connector, True))
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert not snapshot.complete
    assert b"synthetic-private-value" not in snapshot.raw


def test_postgres_context_and_properties_real_server(recorder, monkeypatch):
    require_server()

    def operation():
        with connector() as conn:
            assert not conn.autocommit and not conn.closed and not conn.broken
            conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
            assert conn.isolation_level == psycopg.IsolationLevel.READ_COMMITTED
            with conn.cursor() as cursor:
                assert cursor.execute("select 7") is cursor
                assert cursor.description[0].name == "?column?"
                result = cursor.fetchone(), cursor.statusmessage, conn.info.server_version
            conn.commit()
            conn.autocommit = True
            assert conn.autocommit
        return result, conn.closed

    recorder.run_sync(operation)
    snapshot = saved(recorder)
    monkeypatch.setattr(psycopg, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).reproduced


def test_postgres_fresh_guarded_worker_real_server(recorder):
    require_server()
    from rewind import replay_file

    recorder.run_sync(lambda: typed_values(connector, True))
    snapshot = saved(recorder)
    report = replay_file(
        recorder.store.path / f"{snapshot.id}.rewind.json",
        "tests.relational_helpers:postgres_target",
    )
    assert report.reproduced, report


def test_postgres_capture_limit_preserves_query_result(recorder):
    require_server()
    from rewind import Limits

    recorder.limits = Limits(interactions=2)
    assert recorder.run_sync(lambda: typed_values(connector, True))[0] == 12.5
    assert not recorder.store.load(recorder.store.ids()[0]).complete


def test_postgres_generator_bindings_are_not_preconsumed(recorder):
    require_server()
    observed = []

    def rows():
        for value in range(3):
            observed.append(value)
            yield (value,)

    def operation():
        conn = connector()
        cursor = conn.cursor()
        cursor.execute("create temporary table rewind_generated(value integer)")
        cursor.executemany("insert into rewind_generated values (%s)", rows())
        result = cursor.rowcount
        conn.close()
        return result

    assert recorder.run_sync(operation) == 3
    assert observed == [0, 1, 2]
    assert not recorder.store.load(recorder.store.ids()[0]).complete


def test_guard_prepares_runtime_but_blocks_subsequent_native_loads():
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
from rewind.errors import RewindError
from rewind.runner import NetworkGuard
check = NetworkGuard()
check.install()
import psycopg
import ctypes
before = check.violations
blocked = []
for loader in (ctypes.CDLL, ctypes.PyDLL):
    try:
        loader(None)
    except RewindError:
        blocked.append(True)
print(json.dumps({"initial": before, "blocked": blocked, "violations": check.violations}))
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"initial": 0, "blocked": [True, True], "violations": 2}
