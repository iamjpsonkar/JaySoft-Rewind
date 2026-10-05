#!/usr/bin/env python3
"""Repeatable synthetic capture measurements; no performance thresholds."""

import argparse
import asyncio
import gc
import importlib.metadata
import json
import math
import platform
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path

from rewind import CapturePolicy, LocalStore, Retention, Rewind

MODES = ("baseline", "disabled", "discarded", "synchronous", "background", "storm")


def percentile(values: list[float], fraction: float) -> float:
    """Linear interpolation between adjacent sorted samples (including endpoints)."""
    if not values or not 0 <= fraction <= 1:
        raise ValueError("percentile requires samples and a fraction between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(durations_ns: list[int], wall_ns: int, cpu_ns: int, drain_ns: int) -> dict:
    if not durations_ns or wall_ns <= 0 or cpu_ns < 0 or drain_ns < 0:
        raise ValueError("invalid duration measurement")
    micros = [duration / 1000 for duration in durations_ns]
    count = len(micros)
    return {
        "operations": count,
        "latency_us": {f"p{p}": percentile(micros, p / 100) for p in (50, 95, 99)},
        "wall_seconds": wall_ns / 1e9,
        "process_cpu_seconds": cpu_ns / 1e9,
        "drain_seconds": drain_ns / 1e9,
        "throughput_ops_per_second": count * 1e9 / wall_ns,
        "including_drain_ops_per_second": count * 1e9 / (wall_ns + drain_ns),
    }


def deltas(value: float, baseline: float) -> dict:
    return {
        "absolute": value - baseline,
        "relative_percent": (value / baseline - 1) * 100 if baseline else None,
    }


def rss_high_water() -> dict:
    """ru_maxrss is a process lifetime maximum, with OS-dependent original units."""
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, AttributeError):
        return {"bytes": None, "scope": "unavailable"}
    if sys.platform == "darwin":
        multiplier, source_unit = 1, "bytes"
    elif sys.platform.startswith("linux"):
        multiplier, source_unit = 1024, "KiB"
    else:
        return {"bytes": None, "scope": "unsupported platform unit"}
    return {
        "bytes": int(value * multiplier),
        "source_unit": source_unit,
        "scope": "process lifetime high-water mark; shared across all modes",
    }


async def workload(payload: str, kind: str, io_delay: float) -> int:
    # Fixed arithmetic avoids random seeds, native clients, and external services.
    checksum = sum((index * 17) % 251 for index in range(128)) + len(payload)
    if kind == "io":
        await asyncio.sleep(io_delay)
    return checksum


async def execute(invoke, iterations: int, concurrency: int) -> list[int]:
    samples = []

    async def worker(worker_index):
        for _ in range(worker_index, iterations, concurrency):
            start = time.perf_counter_ns()
            await invoke()
            samples.append(time.perf_counter_ns() - start)

    await asyncio.gather(*(worker(index) for index in range(min(concurrency, iterations))))
    return samples


def make_recorder(mode: str, path: Path, args):
    writer = None
    store = None
    if mode in ("synchronous", "background"):
        store = LocalStore(path, max_bytes=1024 * 1024 * 1024)
    if mode == "background":
        from rewind.persistence import BackgroundWriter

        writer = BackgroundWriter(store, max_items=args.queue_items, max_bytes=args.queue_bytes)
    recorder = Rewind(
        application="capture-benchmark",
        code_paths=[__file__],
        store=store if writer is None else None,
        **({"writer": writer} if writer is not None else {}),
        policy=CapturePolicy.synthetic(),
        retain=Retention(always=mode in ("synchronous", "background")),
        enabled=mode != "disabled",
    )
    return recorder, writer, store


async def trial(mode: str, kind: str, args, iterations: int, *, memory: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="rewind-benchmark-") as temporary:
        recorder, writer, store = make_recorder(mode, Path(temporary) / "store", args)
        payload = "x" * args.payload_bytes

        async def invoke():
            if mode == "baseline":
                return await workload(payload, kind, args.io_delay)
            return await recorder.run(workload, payload, kind, args.io_delay)

        if memory:
            gc.collect()
            tracemalloc.start()
        try:
            cpu_start = time.process_time_ns()
            started = time.perf_counter_ns()
            samples = await execute(invoke, iterations, args.concurrency)
            wall_ns = time.perf_counter_ns() - started
            cpu_ns = time.process_time_ns() - cpu_start
            drain_start = time.perf_counter_ns()
            shutdown = None
            if writer is not None:
                shutdown = await asyncio.to_thread(writer.close, timeout=args.drain_timeout)
                if not shutdown.drained or shutdown.worker_alive:
                    raise RuntimeError("background writer did not drain before timeout")
            drain_ns = time.perf_counter_ns() - drain_start if writer is not None else 0
            total_cpu_ns = time.process_time_ns() - cpu_start
            peak = tracemalloc.get_traced_memory()[1] if memory else None
        finally:
            if memory:
                tracemalloc.stop()
            if writer is not None:
                writer.close(timeout=args.drain_timeout)
        artifacts = list(store.path.glob("*.rewind.json")) if store is not None else []
        result = summarize(samples, wall_ns, cpu_ns, drain_ns)
        result.update(
            including_drain_process_cpu_seconds=total_cpu_ns / 1e9,
            artifact_count=len(artifacts),
            artifact_bytes=sum(item.stat().st_size for item in artifacts),
            capture_stats=recorder.stats()
            if hasattr(recorder, "stats")
            else dict(recorder.metrics),
            writer_stats=writer.stats() if writer is not None else None,
            shutdown=asdict(shutdown) if shutdown is not None else None,
        )
        if memory:
            result["tracemalloc_peak_bytes"] = peak
        return result


class GatedStore(LocalStore):
    """Hold the first disk write until the storm has filled the queue budget."""

    def __init__(self, path):
        super().__init__(path, max_bytes=1024 * 1024 * 1024)
        self.entered = threading.Event()
        self.release = threading.Event()

    def save(self, snapshot):
        self.entered.set()
        if not self.release.wait(timeout=60):
            raise RuntimeError("storm gate timed out")
        return super().save(snapshot)


def storm_invariants(before: dict, after: dict, *, attempts: int, args) -> dict:
    return {
        "pending_items_within_budget": 0 <= before["pending_items"] <= args.queue_items,
        "pending_bytes_within_budget": 0 <= before["pending_bytes"] <= args.queue_bytes,
        "peak_items_within_budget": 0 <= after["peak_pending_items"] <= args.queue_items,
        "peak_bytes_within_budget": 0 <= after["peak_pending_bytes"] <= args.queue_bytes,
        "admissions_accounted": before["submitted"] + before["rejected"] == attempts,
        "accepted_bytes_are_reserved": (
            before["pending_items"] == before["submitted"]
            and (before["pending_bytes"] > 0) == (before["pending_items"] > 0)
        ),
        "capacity_rejection_observed": before["rejected"] > 0,
        "drained_items": after["pending_items"] == 0 and after["pending_bytes"] == 0,
        "accepted_writes_saved": after["saved"] == before["submitted"],
        "no_store_failures": after["failed"] == 0 and after["dropped"] == 0,
    }


async def storm(args) -> dict:
    from rewind.persistence import BackgroundWriter

    with tempfile.TemporaryDirectory(prefix="rewind-storm-") as temporary:
        store = GatedStore(Path(temporary) / "store")
        writer = BackgroundWriter(store, max_items=args.queue_items, max_bytes=args.queue_bytes)
        recorder = Rewind(
            application="capture-storm",
            code_paths=[__file__],
            writer=writer,
            policy=CapturePolicy.synthetic(),
        )
        payload = "x" * args.payload_bytes

        async def fail(value):
            await asyncio.sleep(0)
            raise ValueError("synthetic failure " + value)

        async def invoke():
            try:
                await recorder.run(fail, payload)
            except ValueError:
                pass

        attempts = max(args.iterations, args.queue_items + 2)
        started = time.perf_counter_ns()
        try:
            await invoke()
            submitted = writer.stats()["submitted"]
            entered = await asyncio.to_thread(store.entered.wait, 5) if submitted else False
            if submitted and not entered:
                raise RuntimeError("background store did not enter the controlled gate")
            samples = await execute(invoke, attempts - 1, args.concurrency)
            before = writer.stats()
        finally:
            store.release.set()
            drain_start = time.perf_counter_ns()
            shutdown = await asyncio.to_thread(writer.close, timeout=args.drain_timeout)
            drain_ns = time.perf_counter_ns() - drain_start
        after = writer.stats()
        checks = storm_invariants(before, after, attempts=attempts, args=args)
        checks["worker_stopped"] = not shutdown.worker_alive and shutdown.drained
        artifacts = list(store.path.glob("*.rewind.json"))
        checks["saved_artifacts_accounted"] = len(artifacts) == after["saved"]
        return {
            "attempts": attempts,
            "in_flight_exercised": entered,
            "latency_us_after_first": {
                f"p{p}": percentile([sample / 1000 for sample in samples], p / 100)
                for p in (50, 95, 99)
            },
            "wall_including_gate_and_drain_seconds": (time.perf_counter_ns() - started) / 1e9,
            "drain_seconds": drain_ns / 1e9,
            "before_release": before,
            "after_drain": after,
            "artifact_count": len(artifacts),
            "artifact_bytes": sum(item.stat().st_size for item in artifacts),
            "shutdown": asdict(shutdown),
            "invariants": checks,
            "passed": all(checks.values()),
        }


def aggregate(trials: list[dict]) -> dict:
    return {
        "latency_us": {
            key: statistics.median(trial["latency_us"][key] for trial in trials)
            for key in ("p50", "p95", "p99")
        },
        **{
            key: statistics.median(trial[key] for trial in trials)
            for key in (
                "wall_seconds",
                "process_cpu_seconds",
                "including_drain_process_cpu_seconds",
                "drain_seconds",
                "throughput_ops_per_second",
                "including_drain_ops_per_second",
            )
        },
    }


async def benchmark(args) -> dict:
    versions = {}
    for package in ("jaysoft-rewind", "httpx", "fastapi", "starlette"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    result = {
        "schema_version": 1,
        "metadata": {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "versions": versions,
            "clock": "perf_counter_ns",
            "cpu_clock": "process_time_ns",
            "settings": {key: value for key, value in vars(args).items() if key != "output"},
        },
        "measurements": {},
    }
    for kind in args.workloads:
        measurements = result["measurements"][kind] = {}
        for mode in args.modes:
            if mode == "storm":
                continue
            if args.warmup:
                await trial(mode, kind, args, args.warmup)
            trials = [await trial(mode, kind, args, args.iterations) for _ in range(args.repeats)]
            measurements[mode] = {"trials": trials, "median": aggregate(trials)}
            measurements[mode]["completion_counts"] = {
                "measured_operations": sum(trial["operations"] for trial in trials),
                "persisted_artifacts": sum(trial["artifact_count"] for trial in trials),
                "writer_rejected": sum(
                    (trial["writer_stats"] or {}).get("rejected", 0) for trial in trials
                ),
                "storage_failures": sum(
                    trial["capture_stats"].get("persistence_failed", 0) for trial in trials
                ),
                "comparison_caveat": (
                    "Completion throughput includes calls whose snapshots were rejected; "
                    "compare persisted counts before interpreting speed differences."
                    if mode == "background"
                    else None
                ),
            }
            if args.memory_iterations:
                memory = await trial(mode, kind, args, args.memory_iterations, memory=True)
                measurements[mode]["memory_pass"] = {
                    "iterations": args.memory_iterations,
                    "tracemalloc_peak_bytes": memory["tracemalloc_peak_bytes"],
                    "writer_stats": memory["writer_stats"],
                }
        if "baseline" in measurements:
            baseline = measurements["baseline"]["median"]
            for measurement in measurements.values():
                median = measurement["median"]
                measurement["delta_from_baseline"] = {
                    "p50_latency_us": deltas(
                        median["latency_us"]["p50"], baseline["latency_us"]["p50"]
                    ),
                    "throughput_ops_per_second": deltas(
                        median["throughput_ops_per_second"], baseline["throughput_ops_per_second"]
                    ),
                }
    if "storm" in args.modes:
        result["storm"] = await storm(args)
    result["metadata"]["rss_high_water"] = rss_high_water()
    return result


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def nonnegative_int(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def finite_positive(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def choices(value, allowed):
    selected = value.split(",")
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(x not in allowed for x in selected)
    ):
        raise argparse.ArgumentTypeError(
            "choose distinct comma-separated values: " + ",".join(allowed)
        )
    return selected


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", type=lambda value: choices(value, MODES), default=list(MODES))
    parser.add_argument(
        "--workloads", type=lambda value: choices(value, ("cpu", "io")), default=["cpu", "io"]
    )
    parser.add_argument("--iterations", type=positive_int, default=100)
    parser.add_argument("--warmup", type=nonnegative_int, default=10)
    parser.add_argument("--repeats", type=positive_int, default=3)
    parser.add_argument("--concurrency", type=positive_int, default=4)
    parser.add_argument("--payload-bytes", type=nonnegative_int, default=256)
    parser.add_argument("--queue-items", type=positive_int, default=32)
    parser.add_argument("--queue-bytes", type=positive_int, default=1024 * 1024)
    parser.add_argument("--io-delay", type=finite_positive, default=0.001)
    parser.add_argument("--drain-timeout", type=finite_positive, default=30)
    parser.add_argument("--memory-iterations", type=nonnegative_int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(benchmark(args))
        encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            args.output.write_text(encoded, encoding="utf-8")
        return 0 if result.get("storm", {}).get("passed", True) else 1
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        parser.exit(2, f"benchmark failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
