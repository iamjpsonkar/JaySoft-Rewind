import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from examples.fastapi_failure import record as record_asgi
from examples.http_failure import record
from rewind import replay_file
from rewind.cli import generate_test, main


async def test_real_cli_fresh_process_replays_both_examples(tmp_path):
    for capture, factory in [
        (record, "examples.http_failure:replay_target"),
        (record_asgi, "examples.fastapi_failure:replay_target"),
    ]:
        artifact = await capture(tmp_path / factory.split(":")[0])
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "rewind", "replay", str(artifact), "--app", factory],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        report = json.loads(result.stdout)
        assert report["status"] == "reproduced"
        assert report["isolation"] == "python-guard"


@pytest.mark.parametrize("factory", [
    "network_during_import_factory",
    "swallowed_network_factory",
    pytest.param(
        "swallowed_sendmsg_factory",
        marks=pytest.mark.skipif(
            not hasattr(socket.socket, "sendmsg"), reason="sendmsg is unavailable on this platform"
        ),
    ),
    "swallowed_name_lookup_factory",
])
async def test_worker_blocks_network_before_factory_and_cannot_hide_attempt(tmp_path, factory):
    artifact = await record(tmp_path / "recordings")
    report = replay_file(artifact, f"tests.runner_targets:{factory}")
    assert report.status == "replay_error"
    assert "blocked external operation" in report.detail


async def test_worker_has_a_parent_enforced_timeout(tmp_path):
    artifact = await record(tmp_path / "recordings")
    report = replay_file(artifact, "tests.runner_targets:timeout_factory", timeout=0.5)
    assert report.status == "replay_error"
    assert "timeout" in report.detail


async def test_cli_exit_code_distinguishes_divergence(tmp_path, capsys):
    artifact = await record(tmp_path / "recordings")
    assert (
        main(["replay", str(artifact), "--app", "tests.runner_targets:changed_outcome_factory"])
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "diverged"


async def test_generated_reproduction_test_executes(tmp_path):
    artifact = await record(tmp_path / "recordings")
    output = tmp_path / "test_reproduction.py"
    generate_test(artifact, "examples.http_failure:replay_target", output)
    assert output.exists()
    assert output.with_suffix(".rewind.json").stat().st_mode & 0o777 == 0o600
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(Path.cwd()), str(Path.cwd() / "src")])
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "pytest", str(output), "-q"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    with pytest.raises(FileExistsError):
        generate_test(artifact, "examples.http_failure:replay_target", output)


async def test_inspect_list_delete_and_no_application_import(tmp_path, capsys):
    artifact = await record(tmp_path / "recordings")
    assert main(["inspect", str(artifact)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["complete"] is True
    assert main(["list", "--store", str(artifact.parent)]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert main(["delete", summary["id"], "--store", str(artifact.parent)]) == 0
    assert not artifact.exists()


def test_untrusted_terminal_data_is_not_printed_raw(tmp_path, capsys):
    bad = tmp_path / "bad.rewind.json"
    bad.write_text('{"schema_version": "\\u001b[2J"}')
    assert main(["inspect", str(bad)]) == 3
    output = capsys.readouterr()
    assert "\x1b" not in output.out + output.err
