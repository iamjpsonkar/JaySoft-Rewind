"""Check workload accounting, independent processes, budgets, and rollback evidence."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_deployment as validation

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile():
    value = json.loads((ROOT / "docs/validation/local-staging.json").read_text())
    value.update(requests=4, memory_requests=3, warmup_requests=0, queue_items=2,
                 modes=["direct", "background"])
    return value


@pytest.mark.parametrize(
    "field,value",
    [("schema_version", True), ("requests", 0), ("requests", True), ("queue_items", 0),
     ("offered_rate_per_second", float("nan")), ("process_timeout_seconds", float("inf")),
     ("modes", ["direct", "direct"]), ("modes", ["background"]), ("modes", ["unknown"])],
)
def test_invalid_profile_rejected_before_factory_import(profile, field, value, monkeypatch):
    monkeypatch.setattr(validation.importlib, "import_module",
                        lambda name: pytest.fail("configuration must not import a factory"))
    profile[field] = value
    with pytest.raises(ValueError):
        validation.validate_profile(profile)


def test_profiles_cannot_choose_imports_or_unknown_budget_names(profile):
    profile["factory"] = "untrusted:callback"
    with pytest.raises(ValueError):
        validation.validate_profile(profile)
    del profile["factory"]
    profile["budgets"]["default"]["unrecognized"] = 5
    with pytest.raises(ValueError):
        validation.validate_profile(profile)
    with pytest.raises(ValueError, match="duplicate"):
        validation.read_profile('{"name":"first","name":"second"}')


def test_missing_nonfinite_and_over_budget_measurements_fail():
    report = validation.evaluate(
        {"exact": 4, "too_large": 5, "unavailable": None, "nonfinite": float("nan")},
        {"exact": 4, "too_large": 4, "unavailable": 4, "nonfinite": 4, "absent": 4},
    )
    assert report["exact"]["passed"]
    assert not any(report[key]["passed"] for key in report if key != "exact")


async def test_open_loop_keeps_offering_work_while_workers_are_blocked(profile, monkeypatch):
    all_offered = asyncio.Event()
    release = asyncio.Event()
    offered = 0
    original = asyncio.Queue
    profile.update(concurrency=1, backlog_items=1, offered_rate_per_second=100_000)

    class ObservedQueue(original):
        def put_nowait(self, item):
            nonlocal offered
            if item is not None:
                offered += 1
                if offered == 20:
                    all_offered.set()
            return super().put_nowait(item)

    monkeypatch.setattr(validation.asyncio, "Queue", ObservedQueue)

    async def invoke(index):
        await release.wait()

    task = asyncio.create_task(validation.offered_load(invoke, profile, 20))
    try:
        await asyncio.wait_for(all_offered.wait(), timeout=5)
        assert not task.done()
    finally:
        release.set()
    result = await task
    assert result["offered"] == 20
    assert result["admission_rejections"] > 0
    assert result["admitted"] == result["completed"]
    assert result["completed"] + result["admission_rejections"] == 20
    assert result["peak_request_queue_items"] == result["peak_active_requests"] == 1
    assert result["latency_ms"]["p95"] >= result["queue_wait_ms"]["p50"]


async def test_application_failures_are_counted_without_exception_text(profile):
    async def invoke(index):
        if index == 1:
            raise ValueError("private-application-secret")

    result = await validation.offered_load(invoke, profile, 3)
    assert result["completed"] == 3 and result["application_errors"] == 1
    assert "private-application-secret" not in json.dumps(result)


async def test_drill_exercises_live_capture_disable_saturation_drain_and_rollback(profile):
    result = await validation.drill(profile, "builtin:synthetic")
    assert result["passed"], result["checks"]
    assert result["checks"]["active_request_survives_disable"]
    assert result["checks"]["rollback_direct_matches_baseline"]
    assert result["before_gate_release"]["in_flight"]
    assert result["before_gate_release"]["rejected"] > 0
    assert result["after_first_drain"]["pending_items"] == 0
    assert result["final_writer"]["saved"] == result["final_writer"]["submitted"]
    assert not result["shutdown"]["worker_alive"]


async def test_all_rejected_does_not_claim_inflight_drill_was_exercised(profile):
    profile["queue_bytes"] = 1
    result = await validation.drill(profile, "builtin:synthetic")
    assert not result["passed"]
    assert not result["checks"]["inflight_store_gate_exercised"]
    assert not result["checks"]["active_request_retained"]


def test_cli_collects_independent_timing_memory_and_drill_processes(profile, tmp_path):
    config = tmp_path / "profile.json"
    output = tmp_path / "report.json"
    config.write_text(json.dumps(profile))
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT))))
    result = subprocess.run(
        [sys.executable, "scripts/validate_deployment.py", "--profile", str(config),
         "--output", str(output)], cwd=ROOT, env=environment, text=True,
        capture_output=True, timeout=45,
    )
    assert result.returncode in (0, 1), result.stderr
    report = json.loads(output.read_text())
    pids = {report["drill"]["pid"]}
    for mode, entry in report["modes"].items():
        assert all(entry["invariants"].values())
        for phase, requests in (("timing", 4), ("memory", 3)):
            measurement = entry[phase]
            assert measurement["load"]["offered"] == requests
            assert measurement["rss_peak_bytes"] > 0
            assert measurement["phase"] == phase
            pids.add(measurement["pid"])
            assert measurement["artifact_count"] == (requests if mode == "background" else 0)
        assert entry["timing"]["tracemalloc_peak_bytes"] is None
        assert entry["memory"]["tracemalloc_peak_bytes"] > 0
    assert len(pids) == 5
    assert report["provenance"]["pid"] not in pids
    assert report["provenance"]["script_sha256"]
    assert report["provenance"]["rewind_source_sha256"]
    assert report["profile"] == profile
    assert report["drill"]["passed"]


def test_explicit_custom_factory_is_supported(profile, tmp_path, monkeypatch):
    module = tmp_path / "custom_staging.py"
    module.write_text(
        "def build(rewind, profile):\n"
        "    async def invoke(index):\n"
        "        rewind.sources.uuid4()\n"
        "        return index + 1\n"
        "    return invoke\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    rewind, _, _ = validation.recorder("direct", tmp_path / "store", profile)
    workload, source = validation.load_workload("custom_staging:build", rewind, profile)
    assert asyncio.run(workload(4)) == 5
    assert source["kind"] == "user-selected" and source["module_sha256"]


def test_child_deadline_is_a_failed_measurement(profile, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 1)

    monkeypatch.setattr(validation.subprocess, "run", timeout)
    assert validation.run_child("direct", "timing", profile, "builtin:synthetic") == {
        "error": "worker_deadline_exceeded"
    }


def test_failed_budget_returns_exit_one_and_writes_evidence(profile, tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    output = tmp_path / "report.json"
    path.write_text(json.dumps(profile))
    monkeypatch.setattr(validation, "validate_deployment", lambda *args: {"passed": False})
    assert validation.main(["--profile", str(path), "--output", str(output)]) == 1
    assert json.loads(output.read_text()) == {"passed": False}
