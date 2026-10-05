"""Validate benchmark accounting and execution without timing assertions."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import benchmark_capture as bench

ROOT = Path(__file__).resolve().parents[1]


def run_benchmark(tmp_path, *arguments):
    report = tmp_path / "report.json"
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT))))
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/benchmark_capture.py"),
            "--iterations",
            "4",
            "--warmup",
            "1",
            "--repeats",
            "2",
            "--concurrency",
            "2",
            "--memory-iterations",
            "2",
            "--queue-items",
            "1",
            "--workloads",
            "cpu,io",
            "--output",
            str(report),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(report.read_text())


def assert_finite(value):
    if isinstance(value, float):
        assert math.isfinite(value)
    elif isinstance(value, dict):
        for item in value.values():
            assert_finite(item)
    elif isinstance(value, list):
        for item in value:
            assert_finite(item)


def test_percentiles_and_delta_units():
    assert bench.percentile([30, 10, 20], 0.5) == 20
    assert bench.percentile([30, 10, 20], 0.95) == 29
    assert bench.percentile([3], 0.99) == 3
    with pytest.raises(ValueError):
        bench.percentile([], 0.5)
    assert bench.deltas(15, 10) == {"absolute": 5, "relative_percent": 50}
    assert bench.deltas(2, 0) == {"absolute": 2, "relative_percent": None}
    summary = bench.summarize([1000, 3000], 100_000, 20_000, 100_000)
    assert summary["latency_us"]["p50"] == 2
    assert summary["throughput_ops_per_second"] == 20_000
    assert summary["including_drain_ops_per_second"] == 10_000
    assert summary["process_cpu_seconds"] == 0.00002


def test_cli_modes_have_independent_counts_and_separate_memory_pass(tmp_path):
    report = run_benchmark(tmp_path, "--modes", "baseline,disabled,discarded,synchronous")
    assert_finite(report)
    assert report["schema_version"] == 1
    assert report["metadata"]["settings"]["repeats"] == 2
    for measurements in report["measurements"].values():
        for mode, entry in measurements.items():
            assert len(entry["trials"]) == 2
            assert entry["memory_pass"]["tracemalloc_peak_bytes"] > 0
            for trial in entry["trials"]:
                assert trial["operations"] == 4
                assert trial["artifact_count"] == (4 if mode == "synchronous" else 0)
                assert trial["latency_us"]["p50"] <= trial["latency_us"]["p95"]
                assert trial["latency_us"]["p95"] <= trial["latency_us"]["p99"]
                assert trial["drain_seconds"] == 0
            expected = bench.deltas(
                entry["median"]["latency_us"]["p50"],
                measurements["baseline"]["median"]["latency_us"]["p50"],
            )
            assert entry["delta_from_baseline"]["p50_latency_us"] == expected


def test_background_and_controlled_storm_account_for_inflight_budget(tmp_path):
    report = run_benchmark(tmp_path, "--modes", "background,storm")
    assert_finite(report)
    storm = report["storm"]
    assert storm["passed"] and all(storm["invariants"].values())
    assert storm["in_flight_exercised"]
    assert storm["before_release"]["pending_items"] == 1
    assert storm["before_release"]["submitted"] == 1
    assert storm["before_release"]["rejected"] == storm["attempts"] - 1
    assert storm["artifact_count"] == 1
    for measurements in report["measurements"].values():
        for trial in measurements["background"]["trials"]:
            stats = trial["writer_stats"]
            assert stats["submitted"] + stats["rejected"] == 4
            assert stats["saved"] == trial["artifact_count"]
            assert stats["pending_items"] == 0
            assert trial["shutdown"]["drained"]


def test_tiny_byte_budget_is_reported_as_all_rejected(tmp_path):
    report = run_benchmark(tmp_path, "--modes", "storm", "--queue-bytes", "1")
    assert report["storm"]["passed"]
    assert not report["storm"]["in_flight_exercised"]
    assert report["storm"]["artifact_count"] == 0
    assert report["storm"]["before_release"]["rejected"] == report["storm"]["attempts"]


def test_byte_budget_saturates_before_item_budget(tmp_path):
    report = run_benchmark(
        tmp_path,
        "--modes",
        "storm",
        "--queue-items",
        "8",
        "--queue-bytes",
        "4096",
    )
    storm = report["storm"]
    assert storm["passed"] and storm["in_flight_exercised"]
    assert 0 < storm["before_release"]["submitted"] < 8
    assert 0 < storm["after_drain"]["peak_pending_bytes"] <= 4096


def test_invalid_storm_accounting_is_detected():
    before = dict(pending_items=2, pending_bytes=101, submitted=2, rejected=0)
    after = dict(
        pending_items=1,
        pending_bytes=1,
        saved=0,
        failed=1,
        dropped=0,
        peak_pending_items=2,
        peak_pending_bytes=101,
    )
    checks = bench.storm_invariants(
        before,
        after,
        attempts=3,
        args=SimpleNamespace(queue_items=1, queue_bytes=100),
    )
    assert not checks["pending_items_within_budget"]
    assert not checks["pending_bytes_within_budget"]
    assert not checks["peak_items_within_budget"]
    assert not checks["peak_bytes_within_budget"]
    assert not checks["admissions_accounted"]
    assert not checks["drained_items"]
    assert not checks["accepted_writes_saved"]
    assert not checks["capacity_rejection_observed"]
    assert not checks["no_store_failures"]


def test_failed_invariant_returns_nonzero(monkeypatch, capsys):
    async def broken(args):
        return {"storm": {"passed": False}}

    monkeypatch.setattr(bench, "benchmark", broken)
    assert bench.main(["--modes", "storm"]) == 1
    assert json.loads(capsys.readouterr().out)["storm"]["passed"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["--iterations", "0"],
        ["--io-delay", "nan"],
        ["--drain-timeout", "inf"],
        ["--modes", "baseline,baseline"],
        ["--workloads", "network"],
    ],
)
def test_invalid_settings_fail_before_running(arguments):
    with pytest.raises(SystemExit) as error:
        bench.main(arguments)
    assert error.value.code == 2
