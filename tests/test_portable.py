import io
import json
import zipfile

import pytest

from rewind import LocalStore
from rewind.cli import main
from rewind.errors import InvalidSnapshot
from rewind.portable import export_snapshot, import_snapshot, read_archive


async def artifact(recorder):
    async def operation():
        return {"fixture": b"bytes", "ordered": [1, 2]}

    await recorder.run(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    return recorder.store.path / f"{snapshot.id}.rewind.json", snapshot


async def test_portable_roundtrip_and_atomic_no_clobber(recorder, tmp_path):
    source, snapshot = await artifact(recorder)
    output = export_snapshot(source, tmp_path / "fixture.rewind")
    assert output.stat().st_mode & 0o077 == 0
    assert read_archive(output).raw == snapshot.raw
    store = LocalStore(tmp_path / "imported")
    destination = import_snapshot(output, store)
    assert destination.read_bytes() == snapshot.raw
    with pytest.raises(FileExistsError):
        import_snapshot(output, store)
    with pytest.raises(FileExistsError):
        export_snapshot(source, output)
    assert destination.read_bytes() == snapshot.raw
    assert read_archive(output).raw == snapshot.raw
    assert not list(tmp_path.glob(".rewind-export-*"))


@pytest.mark.parametrize("attack", ["traversal", "duplicate", "compressed", "hash", "extra"])
async def test_archive_rejects_unsafe_or_corrupt_members(recorder, tmp_path, attack):
    source, _ = await artifact(recorder)
    output = export_snapshot(source, tmp_path / "valid.rewind")
    with zipfile.ZipFile(output) as archive:
        manifest = archive.read("manifest.json")
        payload = archive.read("snapshot.rewind.json")
    if attack == "hash":
        data = json.loads(manifest)
        data["sha256"] = "0" * 64
        manifest = json.dumps(data).encode()
    raw = io.BytesIO()
    compression = zipfile.ZIP_DEFLATED if attack == "compressed" else zipfile.ZIP_STORED
    with zipfile.ZipFile(raw, "w", compression=compression) as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr(
            "../snapshot.rewind.json" if attack == "traversal" else "snapshot.rewind.json",
            payload,
        )
        if attack in ("duplicate", "extra"):
            with pytest.warns(UserWarning) if attack == "duplicate" else no_warning():
                archive.writestr("manifest.json" if attack == "duplicate" else "app.py", b"bad")
    output = tmp_path / "invalid.rewind"
    output.write_bytes(raw.getvalue())
    with pytest.raises(InvalidSnapshot):
        read_archive(output)
    assert not (tmp_path / "snapshot.rewind.json").exists()


def no_warning():
    from contextlib import nullcontext

    return nullcontext()


async def test_cli_export_import_and_doctor_are_data_only(recorder, tmp_path, capsys):
    source, snapshot = await artifact(recorder)
    output = tmp_path / "fixture.rewind"
    assert main(["export", str(source), "--output", str(output)]) == 0
    assert main(["import", str(output), "--store", str(tmp_path / "imported")]) == 0
    assert LocalStore(tmp_path / "imported").load(snapshot.id).raw == snapshot.raw
    capsys.readouterr()
    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "python" in report
    assert "dependencies" in report
    assert "environment" not in report


async def test_export_rejects_symlink_input_and_output(recorder, tmp_path):
    source, _ = await artifact(recorder)
    linked = tmp_path / "symlink"
    linked.symlink_to(source)
    with pytest.raises(OSError):
        export_snapshot(linked, tmp_path / "new.rewind")
    with pytest.raises(FileExistsError):
        export_snapshot(source, linked)
    assert linked.is_symlink()


def test_bounded_archive_read(tmp_path):
    from rewind import Limits

    source = tmp_path / "oversized.rewind"
    source.write_bytes(b"x" * 5000)
    with pytest.raises(InvalidSnapshot):
        read_archive(source, Limits(body_bytes=1, snapshot_bytes=128))
