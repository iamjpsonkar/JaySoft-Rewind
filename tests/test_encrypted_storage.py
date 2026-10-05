import os
import subprocess
import sys
from pathlib import Path

import pytest

from rewind import EncryptedLocalStore, Limits, Rewind
from rewind.encrypted_storage import _OVERHEAD
from rewind.errors import InvalidSnapshot, RewindError


@pytest.fixture
async def snapshot(recorder):
    async def operation():
        return {"value": "plaintext-fixture-marker"}

    await recorder.run(operation)
    return recorder.store.load(recorder.store.ids()[0])


@pytest.mark.parametrize("key_size", [16, 24, 32])
def test_round_trip_private_ciphertext_and_metadata(snapshot, tmp_path, key_size):
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"k" * key_size)
    path = store.save(snapshot)
    assert path.name == f"{snapshot.id}.rewind.enc"
    assert snapshot.raw not in path.read_bytes()
    assert b"plaintext-fixture-marker" not in path.read_bytes()
    assert len(path.read_bytes()) == len(snapshot.raw) + _OVERHEAD
    assert path.stat().st_mode & 0o077 == 0
    assert store.path.stat().st_mode & 0o077 == 0
    assert store.ids() == [snapshot.id]
    assert store.load(snapshot.id).raw == snapshot.raw
    assert [item.name for item in store.path.iterdir()] == [path.name]


def test_no_plaintext_temporary_file_and_fresh_nonce_per_write(snapshot, tmp_path, monkeypatch):
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"k" * 32)
    published = []
    replace = os.replace

    def observe(source, target):
        raw = Path(source).read_bytes()
        assert b"plaintext-fixture-marker" not in raw
        assert snapshot.raw not in raw
        published.append(raw)
        replace(source, target)

    monkeypatch.setattr(os, "replace", observe)
    store.save(snapshot)
    store.save(snapshot)
    assert len(published) == 2 and published[0] != published[1]
    assert store.load(snapshot.id) == snapshot


def test_wrong_key_tampering_and_renaming_are_rejected(snapshot, tmp_path):
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32)
    path = store.save(snapshot)
    original = path.read_bytes()
    wrong = EncryptedLocalStore(store.path, key=b"b" * 32)
    with pytest.raises(InvalidSnapshot, match="authentication failed"):
        wrong.load(snapshot.id)
    for position in (len(original) - 1, 16):
        modified = bytearray(original)
        modified[position] ^= 1
        path.write_bytes(modified)
        with pytest.raises(InvalidSnapshot):
            store.load(snapshot.id)
    path.write_bytes(original)
    other_id = "f" * 32 if snapshot.id != "f" * 32 else "e" * 32
    path.rename(store.path / f"{other_id}.rewind.enc")
    with pytest.raises(InvalidSnapshot, match="authentication failed"):
        store.load(other_id)


@pytest.mark.parametrize("payload", [b"", b"{}", b"REWIND-AESGCM\x00\x02" + b"x" * 40])
def test_unknown_or_truncated_envelope_is_rejected(tmp_path, payload):
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32)
    identity = "a" * 32
    (store.path / f"{identity}.rewind.enc").write_bytes(payload)
    with pytest.raises(InvalidSnapshot, match="envelope"):
        store.load(identity)


@pytest.mark.parametrize("key", [b"", b"short", b"a" * 17, bytearray(32), "a" * 32, None])
def test_invalid_keys_fail_before_creating_store(tmp_path, key):
    path = tmp_path / "not-created"
    with pytest.raises(ValueError, match="AES-GCM key"):
        EncryptedLocalStore(path, key=key)
    assert not path.exists()


def test_ciphertext_read_is_bounded_before_decryption(tmp_path):
    limits = Limits(snapshot_bytes=1024, body_bytes=64)
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32, limits=limits)

    class ForbiddenCipher:
        def decrypt(self, *args):
            pytest.fail("oversized bytes must be rejected before decryption")

    store._cipher = ForbiddenCipher()
    identity = "a" * 32
    (store.path / f"{identity}.rewind.enc").write_bytes(b"x" * (1024 + _OVERHEAD + 1))
    with pytest.raises(InvalidSnapshot, match="byte limit"):
        store.load(identity)


def test_encrypted_quota_counts_envelope_and_no_clobber_preserves_file(snapshot, tmp_path):
    too_small = EncryptedLocalStore(tmp_path / "small", key=b"a" * 32,
                                    max_bytes=len(snapshot.raw))
    with pytest.raises(RewindError, match="quota"):
        too_small.save(snapshot)
    assert too_small.ids() == []
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32,
                                max_bytes=len(snapshot.raw) + _OVERHEAD)
    path = store.save(snapshot, overwrite=False)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        store.save(snapshot, overwrite=False)
    assert path.read_bytes() == before
    assert not list(store.path.glob(".pending-*"))


def test_encrypted_prune_and_delete_follow_same_contract(snapshot, tmp_path):
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32, retention_seconds=1)
    path = store.save(snapshot)
    os.utime(path, (1, 1))
    report = store.prune()
    assert report == {"removed": 1, "removed_bytes": len(snapshot.raw) + _OVERHEAD,
                      "remaining_items": 0, "remaining_bytes": 0}
    assert store.ids() == []
    store.save(snapshot)
    store.delete(snapshot.id)
    assert store.ids() == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
def test_encrypted_load_rejects_fifo_without_waiting(tmp_path):
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32)
    identity = "a" * 32
    os.mkfifo(store.path / f"{identity}.rewind.enc")
    with pytest.raises(InvalidSnapshot, match="regular file"):
        store.load(identity)


def test_core_import_does_not_import_optional_crypto():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    result = subprocess.run(
        [sys.executable, "-c", "import sys, rewind; "
         "assert not any(name.startswith('cryptography') for name in sys.modules)"],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


async def test_capture_and_inprocess_replay_with_encrypted_store(recorder, tmp_path):
    recorder.store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32)

    async def operation():
        return recorder.sources.uuid4()

    await recorder.run(operation)
    artifact = recorder.store.load(recorder.store.ids()[0])
    assert (await recorder.replay(artifact, operation)).reproduced


def test_sync_capture_with_encrypted_store(recorder, tmp_path):
    store = EncryptedLocalStore(tmp_path / "encrypted", key=b"a" * 32)
    rewind = Rewind(application="encrypted-sync", code_paths=[__file__], store=store,
                    policy=recorder.policy, retain=recorder.retain)

    def operation():
        return rewind.sources.uuid4()

    rewind.run_sync(operation)
    artifact = store.load(store.ids()[0])
    assert rewind.replay_sync(artifact, operation).reproduced
