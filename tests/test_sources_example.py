import asyncio
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from examples.sources_failure import record
from rewind import Limits
from rewind.codecs import decode
from rewind.storage import load_file

ROOT = Path(__file__).resolve().parents[1]


async def test_sources_example_restores_observations_without_factories_or_delay(tmp_path):
    artifact = await record(tmp_path / "sources")
    snapshot = load_file(artifact)
    message, observed = decode(snapshot.data["outcome"]["args"], Limits())
    assert message == "synthetic booking failed"
    assert type(observed["created_at"]) is datetime
    assert type(observed["day"]) is date
    assert type(observed["booking_id"]) is UUID
    assert sorted(observed["shuffled"]) == ["seat-a", "seat-b", "seat-c", "seat-d"]
    assert observed["wait"] == "ready"
    assert len(snapshot.data["interactions"]) == 16

    # A fresh interpreter has no recorded RNG state or clock objects. Replacing
    # only Sources' providers avoids disrupting the event loop's actual clocks.
    script = """
import asyncio
import json
import sys
from types import SimpleNamespace
from examples.sources_failure import replay_target
from rewind.storage import load_file
import rewind.sources as sources

def forbidden(*args, **kwargs):
    raise AssertionError("replay invoked a live observation provider")

sources._time = SimpleNamespace(**{name: forbidden for name in (
    "time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns"
)})
sources._random = SimpleNamespace(**{name: forbidden for name in (
    "random", "randint", "uniform", "choice", "sample", "shuffle"
)})
sources._uuid = SimpleNamespace(uuid4=forbidden)
sources.datetime = SimpleNamespace(now=forbidden)
sources.date = SimpleNamespace(today=forbidden)
waits = []
async def no_delay(delay, result=None):
    assert delay == 0, "replay attempted the recorded real delay"
    waits.append(delay)
    await asyncio.sleep(0)
    return result
sources.asyncio = SimpleNamespace(sleep=no_delay)
target = replay_target()
report = asyncio.run(target.rewind.replay(load_file(sys.argv[1]), target.entrypoint))
assert waits == [0], waits
print(json.dumps(report.to_dict()))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(artifact)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "reproduced", report
    assert report["consumed"] == report["total"] == 16


async def test_sources_example_uses_guarded_cli_worker(tmp_path):
    artifact = await record(tmp_path / "sources")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "rewind",
            "replay",
            str(artifact),
            "--app",
            "examples.sources_failure:replay_target",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "reproduced"
    assert report["isolation"] == "python-guard"
    assert report["consumed"] == report["total"] == 16
