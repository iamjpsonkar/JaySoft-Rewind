import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rewind import LocalStore, replay_file
from rewind.codecs import encode
from rewind.comparison import compare_file, generate_comparison_test
from rewind.limits import Limits
from rewind.snapshot import Snapshot

SOURCE = """from rewind import CapturePolicy, LocalStore, Retention, Rewind, ReplayTarget

def factory(store=None):
    rewind = Rewind(application="comparison-fixture", code_paths=[__file__], store=store,
                    policy=CapturePolicy.synthetic(), retain=Retention(always=True))
    async def operation():
        value = rewind.sources.randint(7, 7)
        BODY
    return ReplayTarget(rewind, operation)
"""


async def fixture(tmp_path, monkeypatch, body="return value"):
    source = tmp_path / "comparison_fixture.py"
    source.write_text(SOURCE.replace("BODY", body))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            [
                str(tmp_path),
                str(Path.cwd() / "src"),
                str(Path.cwd()),
            ]
        ),
    )
    namespace = {"__file__": str(source)}
    exec(compile(source.read_text(), str(source), "exec"), namespace)
    store = LocalStore(tmp_path / "recordings")
    target = namespace["factory"](store)
    try:
        await target.rewind.run(target.entrypoint)
    except ValueError:
        pass
    artifact = store.path / f"{store.ids()[0]}.rewind.json"
    return source, artifact


async def test_changed_source_is_explicit_comparison_not_reproduction(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch)
    original = artifact.read_bytes()
    source.write_text(source.read_text() + "\n# changed source set\n")
    strict = replay_file(artifact, "comparison_fixture:factory")
    assert strict.status == "incompatible"
    compared = compare_file(artifact, "comparison_fixture:factory")
    assert compared.matched
    assert compared.status == "matched"
    assert compared.mode == "comparison"
    assert compared.code_changed is True
    assert compared.expected_source == "recorded"
    assert not hasattr(compared, "reproduced")
    assert artifact.read_bytes() == original


async def test_same_source_still_reports_comparison(tmp_path, monkeypatch):
    _, artifact = await fixture(tmp_path, monkeypatch)
    report = compare_file(artifact, "comparison_fixture:factory")
    assert report.matched
    assert report.code_changed is False
    assert report.mode == "comparison"


@pytest.mark.parametrize("body", ["return value + 1", "raise ValueError('changed failure')"])
async def test_outcome_change_diverges_without_explicit_oracle(tmp_path, monkeypatch, body):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text(SOURCE.replace("BODY", body))
    report = compare_file(artifact, "comparison_fixture:factory")
    assert report.status == "diverged"
    assert "outcome" in report.detail


async def test_fixed_code_needs_developer_expected_result(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch, "raise ValueError('original failure')")
    source.write_text(SOURCE.replace("BODY", "return value + 1"))
    assert compare_file(artifact, "comparison_fixture:factory").status == "diverged"
    report = compare_file(artifact, "comparison_fixture:factory", expected_return=8)
    assert report.matched
    assert report.code_changed is True
    assert report.expected_source == "developer"


async def test_explicit_null_is_an_oracle(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text(SOURCE.replace("BODY", "return None"))
    assert compare_file(artifact, "comparison_fixture:factory").status == "diverged"
    assert compare_file(artifact, "comparison_fixture:factory", expected_return=None).matched


async def test_explicit_exception_oracle(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text(SOURCE.replace("BODY", "raise ValueError('expected failure')"))
    expectation = {
        "kind": "exception",
        "type": "builtins.ValueError",
        "args": encode(("expected failure",), Limits()),
    }
    assert compare_file(
        artifact, "comparison_fixture:factory", expected_outcome=expectation
    ).matched


@pytest.mark.parametrize(
    "body",
    [
        "return rewind.sources.randint(8, 8)",
        "return value",
    ],
)
async def test_extra_or_missing_observations_fail_even_with_oracle(tmp_path, monkeypatch, body):
    source, artifact = await fixture(tmp_path, monkeypatch)
    content = SOURCE.replace("BODY", body)
    if body == "return value":
        content = content.replace("value = rewind.sources.randint(7, 7)", "value = 7")
    source.write_text(content)
    report = compare_file(artifact, "comparison_fixture:factory", expected_return=7)
    assert report.status == "diverged"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "other-application"),
        ("python", "99.99"),
        ("dependencies", {"httpx": "99.99"}),
    ],
)
async def test_only_source_digest_can_differ(tmp_path, monkeypatch, field, value):
    _, artifact = await fixture(tmp_path, monkeypatch)
    data = Snapshot.from_bytes(artifact.read_bytes()).data
    data["application"][field] = value
    artifact.write_bytes(Snapshot.from_dict(data).raw)
    assert compare_file(artifact, "comparison_fixture:factory").status == "incompatible"


async def test_policy_mismatch_is_incompatible(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text(source.read_text().replace("CapturePolicy.synthetic()", "CapturePolicy()"))
    assert compare_file(artifact, "comparison_fixture:factory").status == "incompatible"


@pytest.mark.parametrize("invalid", ["malformed", "incomplete"])
async def test_invalid_artifact_does_not_import_application(tmp_path, monkeypatch, invalid):
    source, artifact = await fixture(tmp_path, monkeypatch)
    marker = tmp_path / "imported"
    source.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + source.read_text()
    )
    if invalid == "malformed":
        artifact.write_bytes(b"not-json")
    else:
        data = Snapshot.from_bytes(artifact.read_bytes()).data
        data["capture"] = {"complete": False, "ineligible_reasons": ["test"]}
        artifact.write_bytes(Snapshot.from_dict(data).raw)
    report = compare_file(artifact, "comparison_fixture:factory")
    assert report.status == ("ineligible" if invalid == "incomplete" else "replay_error")
    assert not marker.exists()


async def test_guard_is_installed_before_import_and_sticky(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text(
        """import socket
try:
    socket.getaddrinfo("example.com", 443)
except Exception:
    pass
"""
        + source.read_text()
    )
    report = compare_file(artifact, "comparison_fixture:factory")
    assert report.status == "replay_error"
    assert "blocked external operation" in report.detail


async def test_comparison_timeout_stays_bounded(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text("import time\ntime.sleep(10)\n" + source.read_text())
    report = compare_file(artifact, "comparison_fixture:factory", timeout=0.1)
    assert report.status == "replay_error"
    assert "timeout" in report.detail


async def test_generated_comparison_test_runs_and_preserves_artifact(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch, "raise ValueError('original failure')")
    source.write_text(SOURCE.replace("BODY", "return value + 1"))
    original = artifact.read_bytes()
    destination = tmp_path / "test_expected_result.py"
    generate_comparison_test(artifact, "comparison_fixture:factory", destination, expected_return=8)
    copied = destination.with_suffix(".rewind.json")
    assert copied.read_bytes() == original == artifact.read_bytes()
    assert copied.stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_mode & 0o777 == 0o600
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "pytest", str(destination), "-q"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with pytest.raises(FileExistsError):
        generate_comparison_test(
            artifact, "comparison_fixture:factory", destination, expected_return=8
        )


@pytest.mark.parametrize(
    "expected",
    [
        {"kind": "return", "value": {"t": "unknown", "v": "x"}},
        {"kind": "exception", "type": "builtins.ValueError", "args": {"t": "scalar", "v": 3}},
        {"kind": "unknown"},
    ],
)
def test_invalid_desired_outcome_fails_before_worker(expected, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("invalid oracle started a subprocess")

    monkeypatch.setattr(subprocess, "run", blocked)
    with pytest.raises(Exception) as error:
        compare_file("unused", "unused:factory", expected_outcome=expected)
    assert not isinstance(error.value, AssertionError)


def test_conflicting_oracles_rejected():
    with pytest.raises(ValueError, match="not both"):
        compare_file(
            "unused",
            "unused:factory",
            expected_return=None,
            expected_outcome={"kind": "return", "value": encode(1, Limits())},
        )


def test_sync_target_works_in_fresh_worker(tmp_path, monkeypatch):
    source = tmp_path / "comparison_fixture.py"
    content = SOURCE.replace("async def operation", "def operation").replace("BODY", "return value")
    content = content.replace(
        "ReplayTarget(rewind, operation)", "ReplayTarget(rewind, operation, kind='callable_sync')"
    )
    source.write_text(content)
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            [
                str(tmp_path),
                str(Path.cwd() / "src"),
                str(Path.cwd()),
            ]
        ),
    )
    namespace = {"__file__": str(source)}
    exec(compile(content, str(source), "exec"), namespace)
    store = LocalStore(tmp_path / "recordings")
    target = namespace["factory"](store)
    assert target.rewind.run_sync(target.entrypoint) == 7
    artifact = store.path / f"{store.ids()[0]}.rewind.json"
    assert replay_file(artifact, "comparison_fixture:factory").reproduced
    source.write_text(content.replace("return value", "return value + 1"))
    assert compare_file(artifact, "comparison_fixture:factory", expected_return=8).matched


def test_flask_wsgi_target_works_in_fresh_worker(tmp_path, monkeypatch):
    from werkzeug.test import EnvironBuilder

    from examples.flask_failure import build

    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(Path.cwd() / "src"), str(Path.cwd())]))
    store = LocalStore(tmp_path / "recordings")
    rewind, app = build(store)
    incoming = EnvironBuilder(method="POST", path="/checkout", json={"order_id": "fixture"})
    response = rewind.wsgi(app)(incoming.get_environ(), lambda *args: lambda body: None)
    try:
        list(response)
    finally:
        response.close()
    artifact = store.path / f"{store.ids()[0]}.rewind.json"
    assert replay_file(artifact, "examples.flask_failure:replay_target").reproduced
    assert compare_file(artifact, "examples.flask_failure:replay_target").matched


async def test_changed_code_cannot_open_uninstrumented_sqlite(tmp_path, monkeypatch):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text(
        "import sqlite3\n" + SOURCE.replace("BODY", "sqlite3.connect(':memory:'); return value")
    )
    report = compare_file(artifact, "comparison_fixture:factory", expected_return=7)
    assert report.status == "replay_error"
    assert "blocked external operation" in report.detail


async def test_database_adapter_still_works_with_sqlite_audit_guard(tmp_path, monkeypatch):
    from examples.database_failure import record

    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(Path.cwd() / "src"), str(Path.cwd())]))
    artifact = await record(tmp_path / "database-recordings")
    assert replay_file(artifact, "examples.database_failure:replay_target").reproduced
    assert compare_file(artifact, "examples.database_failure:replay_target").matched
