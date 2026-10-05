import pytest

from rewind import CapturePolicy
from rewind.adapters.filesystem import RecordingFilesystem


def test_filesystem_roundtrip_has_no_disk_access(recorder, tmp_path, monkeypatch):
    files = RecordingFilesystem(tmp_path)

    def operation():
        files.write_text("fixture", "hello")
        assert files.exists("fixture")
        result = files.read_bytes("fixture")
        listing = files.listdir()
        files.unlink("fixture")
        return result, listing

    expected = recorder.run_sync(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.complete
    monkeypatch.setattr(files, "_path", lambda *a: pytest.fail("disk access during replay"))
    assert recorder.replay_sync(snapshot, operation).reproduced
    assert expected[0] == b"hello"


def test_filesystem_default_policy_excludes_content(recorder, tmp_path):
    recorder.policy = CapturePolicy()
    files = RecordingFilesystem(tmp_path)
    assert recorder.run_sync(lambda: files.write_text("fixture", "private payload")) == 15
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert not snapshot.complete
    assert b"private payload" not in snapshot.raw


def test_filesystem_rejects_escape_and_symlink(recorder, tmp_path):
    files = RecordingFilesystem(tmp_path)
    for path in ("../escape", "/absolute"):
        with pytest.raises(ValueError):
            files.read_bytes(path)
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError):
        files.read_bytes("link/file")


def test_filesystem_changed_write_diverges(recorder, tmp_path):
    files = RecordingFilesystem(tmp_path)
    data = b"first"

    def operation():
        return files.write_bytes("file", data)

    recorder.run_sync(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    data = b"second"
    assert recorder.replay_sync(snapshot, operation).status == "diverged"
    assert (tmp_path / "file").read_bytes() == b"first"


def test_filesystem_missing_file_never_claims_lossless_exception(recorder, tmp_path):
    files = RecordingFilesystem(tmp_path)

    def operation():
        try:
            files.read_bytes("missing")
        except FileNotFoundError:
            return "missing"

    assert recorder.run_sync(operation) == "missing"
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert not snapshot.complete
    assert str(tmp_path).encode() not in snapshot.raw
