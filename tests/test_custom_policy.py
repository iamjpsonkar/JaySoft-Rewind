from dataclasses import replace

import pytest

from rewind import CapturePolicy, Snapshot
from rewind.adapters.dbapi import RecordingSQLite
from rewind.codecs import decode
from rewind.errors import InvalidSnapshot
from rewind.limits import Limits


def test_custom_keys_apply_to_values_headers_query_and_bodies():
    policy = replace(CapturePolicy.synthetic(), redacted_keys=("customer_id",))
    reasons = []
    packed = policy.value({"nested": {"customer-id": "fixture-private"}}, Limits(), reasons.append)
    assert decode(packed, Limits()) == {"nested": {"customer-id": "[REDACTED]"}}
    assert policy.headers([(b"customer_id", b"fixture-private")], reasons.append) == [
        ["customer_id", "[REDACTED]"]
    ]
    assert "fixture-private" not in policy.url(
        "https://x.example/?customer_id=fixture-private", reasons.append
    )
    import base64

    body = policy.body(
        b'{"customer_id":"fixture-private"}', "application/json", Limits(), reasons.append
    )
    assert b"fixture-private" not in base64.b64decode(body)
    assert reasons


async def test_custom_policy_roundtrips_and_secret_env_is_ineligible(recorder, monkeypatch):
    recorder.policy = replace(CapturePolicy.synthetic(), redacted_keys=("REGION",))
    monkeypatch.setenv("REGION", "fixture-private")

    async def operation():
        recorder.sources.getenv("REGION")
        return None

    await recorder.run(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.data["policy"]["redacted_keys"] == ["REGION"]
    assert b"fixture-private" not in snapshot.raw
    assert not snapshot.complete
    assert Snapshot.from_bytes(snapshot.raw).raw == snapshot.raw
    data = snapshot.data
    data["policy"]["redacted_keys"] = [1]
    with pytest.raises(InvalidSnapshot):
        Snapshot.from_dict(data)


@pytest.mark.parametrize("method", ["fetchone", "fetchmany", "fetchall", "next"])
async def test_database_column_redaction_preserves_live_rows(recorder, tmp_path, method):
    import sqlite3

    path = str(tmp_path / "fixture.sqlite")
    connection = sqlite3.connect(path)
    connection.execute("create table people (name text, password text)")
    connection.execute("insert into people values (?, ?)", ("fixture", "fixture-private"))
    connection.commit()
    connection.close()

    async def operation():
        connection = RecordingSQLite().connect(path)
        cursor = connection.execute("select * from people")
        result = next(cursor) if method == "next" else getattr(cursor, method)()
        row = result if method in ("fetchone", "next") else result[0]
        assert row == ("fixture", "fixture-private")
        connection.close()
        return row[0]

    assert await recorder.run(operation) == "fixture"
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert not snapshot.complete
    assert b"fixture-private" not in snapshot.raw
    assert "sensitive_value_removed" in snapshot.data["capture"]["ineligible_reasons"]


async def test_database_sensitive_sql_omits_literals_and_bindings(recorder):
    async def operation():
        connection = RecordingSQLite().connect(":memory:")
        cursor = connection.execute("select ? as password", ("fixture-private",))
        assert cursor.fetchone() == ("fixture-private",)
        connection.close()

    await recorder.run(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert b"fixture-private" not in snapshot.raw
    assert not snapshot.complete
