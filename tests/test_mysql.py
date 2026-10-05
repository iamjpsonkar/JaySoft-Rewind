import os

import pytest

from rewind.adapters.mysql import RecordingMySQL, create_engine

from .relational_helpers import (
    blocked,
    exercise,
    require_server,
    saved,
    sqlalchemy_operation,
    typed_values,
)

pymysql = pytest.importorskip("pymysql")


def options():
    return dict(
        host="127.0.0.1",
        port=int(os.environ.get("REWIND_MYSQL_PORT", "53306")),
        user="root",
        password="rewind-fixture",
        database="rewind",
        connect_timeout=5,
    )


def connector():
    return RecordingMySQL().connect(**options())


def test_mysql_dbapi_real_server(recorder, monkeypatch):
    require_server()
    result = recorder.run_sync(lambda: exercise(connector, False))
    assert result[1] == (1, 10) and list(result[2]) == [(2, 20)] and result[3] == [(3, 30)]
    assert result[4] == 4
    snapshot = saved(recorder)
    assert b"rewind-fixture" not in snapshot.raw
    monkeypatch.setattr(pymysql, "connect", blocked)
    assert recorder.replay_sync(snapshot, lambda: exercise(connector, False)).reproduced


def test_mysql_typed_results_real_server(recorder, monkeypatch):
    require_server()
    recorder.run_sync(lambda: typed_values(connector, False))
    monkeypatch.setattr(pymysql, "connect", blocked)
    assert recorder.replay_sync(saved(recorder), lambda: typed_values(connector, False)).reproduced


def test_mysql_sqlalchemy_real_server(recorder, monkeypatch):
    require_server()

    def factory():
        return create_engine(connect_args=options())

    def operation():
        return sqlalchemy_operation(factory, False)

    assert recorder.run_sync(operation) == (1, 42)
    snapshot = saved(recorder)
    monkeypatch.setattr(pymysql, "connect", blocked)
    report = recorder.replay_sync(snapshot, operation)
    assert report.reproduced, report


def test_mysql_error_and_transaction_options_real_server(recorder, monkeypatch):
    require_server()

    def operation():
        conn = connector()
        assert not conn.get_autocommit()
        cursor = conn.cursor()
        cursor.execute("create temporary table rewind_unique(value integer primary key)")
        cursor.execute("insert into rewind_unique values (1)")
        conn.commit()
        try:
            cursor.execute("insert into rewind_unique values (1)")
        except pymysql.IntegrityError as error:
            result = error.args
        conn.rollback()
        conn.autocommit(True)
        assert conn.get_autocommit()
        conn.close()
        return result

    assert recorder.run_sync(operation)[0] == 1062
    snapshot = saved(recorder)
    monkeypatch.setattr(pymysql, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).reproduced


def test_mysql_changed_query_fails_closed(recorder, monkeypatch):
    require_server()
    changed = False

    def operation():
        conn = connector()
        cursor = conn.cursor()
        cursor.execute("select %s", (2 if changed else 1,))
        row = cursor.fetchone()
        conn.close()
        return row

    recorder.run_sync(operation)
    snapshot = saved(recorder)
    changed = True
    monkeypatch.setattr(pymysql, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).status == "diverged"


def test_mysql_rejects_custom_sqlalchemy_pool():
    with pytest.raises(ValueError, match="custom"):
        create_engine(poolclass=object)


def test_mysql_orm_generated_identity_real_server(recorder, monkeypatch):
    require_server()
    from .relational_helpers import orm_operation

    def operation():
        return orm_operation(lambda: create_engine(connect_args=options()))

    assert recorder.run_sync(operation) == ((1,), (1, "fixture"))
    snapshot = saved(recorder)
    monkeypatch.setattr(pymysql, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).reproduced


def test_mysql_redacts_sensitive_columns_real_server(recorder):
    require_server()
    from .relational_helpers import private_rows

    recorder.run_sync(lambda: private_rows(connector, False))
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert not snapshot.complete
    assert b"synthetic-private-value" not in snapshot.raw


def test_mysql_context_and_execute_count_real_server(recorder, monkeypatch):
    require_server()

    def operation():
        with connector() as conn:
            with conn.cursor() as cursor:
                assert cursor.execute("select 7") == 1
                result = cursor.fetchone(), conn.character_set_name(), conn.get_server_info()
            conn.ping(False)
            conn.commit()
        return result, conn.open

    recorder.run_sync(operation)
    snapshot = saved(recorder)
    monkeypatch.setattr(pymysql, "connect", blocked)
    assert recorder.replay_sync(snapshot, operation).reproduced


def test_mysql_fresh_guarded_worker_real_server(recorder):
    require_server()
    from rewind import replay_file

    recorder.run_sync(lambda: typed_values(connector, False))
    snapshot = saved(recorder)
    report = replay_file(
        recorder.store.path / f"{snapshot.id}.rewind.json", "tests.relational_helpers:mysql_target"
    )
    assert report.reproduced, report


def test_mysql_capture_limit_preserves_query_result(recorder):
    require_server()
    from rewind import Limits

    recorder.limits = Limits(interactions=2)
    assert recorder.run_sync(lambda: typed_values(connector, False))[0] == 12.5
    assert not recorder.store.load(recorder.store.ids()[0]).complete


def test_mysql_generator_bindings_are_not_preconsumed(recorder):
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
