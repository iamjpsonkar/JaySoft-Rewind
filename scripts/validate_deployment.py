#!/usr/bin/env python3
"""Run isolated, fixed-arrival staging measurements and an operational rollback drill."""

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rewind import CapturePolicy, LocalStore, Retention, Rewind, __version__
from rewind.persistence import BackgroundWriter

MODES = ("direct", "disabled", "active", "retained", "background")
BUDGETS = {
    "latency_p95_ms", "schedule_lag_p95_ms", "queue_wait_p95_ms",
    "admission_rejection_fraction", "application_error_fraction", "capture_rejection_fraction",
    "rss_peak_bytes", "tracemalloc_peak_bytes", "drain_seconds", "writer_queue_lag_p95_ms",
}
INTEGER_LIMITS = {
    "requests": (1, 100_000), "memory_requests": (1, 100_000),
    "warmup_requests": (0, 1_000), "concurrency": (1, 1_024),
    "backlog_items": (1, 100_000), "payload_bytes": (0, 65_536),
    "queue_items": (1, 1_024), "queue_bytes": (1, 1_073_741_824),
    "store_bytes": (1, 1_073_741_824),
}
NUMBER_LIMITS = {
    "offered_rate_per_second": (0.001, 100_000), "io_delay_ms": (0, 60_000),
    "store_delay_ms": (0, 60_000), "drain_timeout_seconds": (0.001, 60),
    "process_timeout_seconds": (1, 300),
}


def _number(value: Any, lower: float, upper: float) -> bool:
    return (
        type(value) in (int, float) and lower <= value <= upper and math.isfinite(value)
    )


def validate_profile(value: Any) -> dict:
    required = set(INTEGER_LIMITS) | set(NUMBER_LIMITS) | {
        "schema_version", "name", "modes", "budgets"
    }
    if (
        type(value) is not dict or set(value) != required
        or type(value["schema_version"]) is not int or value["schema_version"] != 1
    ):
        raise ValueError("profile must contain exactly the documented schema-version-1 fields")
    if type(value["name"]) is not str or not 1 <= len(value["name"]) <= 128:
        raise ValueError("profile name must contain 1 to 128 characters")
    for name, (lower, upper) in INTEGER_LIMITS.items():
        if type(value[name]) is not int or not lower <= value[name] <= upper:
            raise ValueError(f"invalid integer setting: {name}")
    for name, (lower, upper) in NUMBER_LIMITS.items():
        if not _number(value[name], lower, upper):
            raise ValueError(f"invalid numeric setting: {name}")
    modes = value["modes"]
    if (
        type(modes) is not list or not modes or any(type(mode) is not str for mode in modes)
        or len(set(modes)) != len(modes) or any(mode not in MODES for mode in modes)
        or "direct" not in modes
    ):
        raise ValueError("modes must be distinct supported modes including direct")
    budgets = value["budgets"]
    if type(budgets) is not dict or set(budgets) != {"default", "modes", "drill"}:
        raise ValueError("budgets require default, modes, and drill mappings")
    if type(budgets["default"]) is not dict or set(budgets["default"]) != BUDGETS:
        raise ValueError("default budgets must specify every documented metric")
    if type(budgets["modes"]) is not dict or set(budgets["modes"]) - set(modes):
        raise ValueError("budget overrides must name selected modes")
    for group in [budgets["default"], *budgets["modes"].values()]:
        if type(group) is not dict or set(group) - BUDGETS:
            raise ValueError("unknown budget metric")
        for name, limit in group.items():
            if not _number(limit, 0, 1e15):
                raise ValueError(f"invalid numeric budget: {name}")
            if name.endswith("fraction") and limit > 1:
                raise ValueError("fraction budgets must lie between zero and one")
    drill = budgets["drill"]
    if type(drill) is not dict or set(drill) != {"drain_seconds", "rss_peak_bytes"}:
        raise ValueError("drill budgets require drain_seconds and rss_peak_bytes")
    if any(not _number(limit, 0, 1e15) for limit in drill.values()):
        raise ValueError("invalid drill budget")
    return value


def _unique(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def read_profile(raw: str) -> dict:
    if len(raw) > 65_536:
        raise ValueError("profile exceeds 64 KiB")
    return validate_profile(json.loads(raw, object_pairs_hook=_unique))


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return {"p50": at(0.5), "p95": at(0.95), "p99": at(0.99), "max": ordered[-1]}


def rss_bytes() -> int | None:
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, AttributeError):
        return None
    if sys.platform == "darwin":
        return int(value)
    if sys.platform.startswith("linux"):
        return int(value * 1024)
    return None


def provenance(factory: str) -> dict:
    import rewind

    root = Path(__file__).resolve().parents[1]

    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, text=True,
                check=True, timeout=5,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    versions = {}
    for package in ("jaysoft-rewind", "httpx", "redis", "sqlalchemy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    source = Path(rewind.__file__).parent
    digest = hashlib.sha256()
    for item in sorted(source.rglob("*.py")):
        digest.update(str(item.relative_to(source)).encode())
        digest.update(item.read_bytes())
    status = git("status", "--porcelain")
    return {
        "utc": datetime.now(UTC).isoformat(), "pid": os.getpid(),
        "python": sys.version, "executable": Path(sys.executable).name,
        "platform": platform.platform(), "machine": platform.machine(),
        "package_version": __version__, "installed_versions": versions,
        "git_revision": git("rev-parse", "HEAD"), "git_dirty": status != "" if status is not None
        else None,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rewind_source_path": "rewind", "rewind_source_sha256": digest.hexdigest(),
        "factory": factory,
    }


class SyntheticWorkload:
    """Actual adapters, local SQLite, and explicitly simulated HTTP/Redis services."""

    def __init__(self, rewind: Rewind, profile: dict) -> None:
        import httpx

        from rewind.adapters.dbapi import RecordingSQLite
        from rewind.adapters.redis import RecordingRedis

        class Cache:
            def get(self, key):
                return b"synthetic-cache-hit"

        self.rewind = rewind
        self.profile = profile
        self.database = RecordingSQLite(dependency="staging-sqlite")
        self.cache = RecordingRedis(Cache(), dependency="staging-cache")
        self.http = httpx.AsyncClient(transport=rewind.httpx_transport(httpx.MockTransport(
            lambda request: httpx.Response(200, json={"amount": 17})
        ), dependency="staging-http"))
        self.payload = "x" * profile["payload_bytes"]

    async def __call__(self, index: int) -> dict:
        connection = self.database.connect(":memory:")
        try:
            row = connection.execute("select ? + 1", (index,)).fetchone()
        finally:
            connection.close()
        cached = self.cache.get("fixture")
        response = await self.http.post(
            "https://fixture.invalid/value", json={"body": self.payload}
        )
        self.rewind.sources.uuid4()
        self.rewind.sources.time()
        await asyncio.sleep(self.profile["io_delay_ms"] / 1000)
        # Return a stable application projection for the rollback-equivalence drill.
        return {"index": row[0], "amount": response.json()["amount"], "cached": cached}

    async def aclose(self) -> None:
        await self.http.aclose()


def load_workload(factory: str, rewind: Rewind, profile: dict) -> tuple[Any, dict]:
    if factory == "builtin:synthetic":
        return SyntheticWorkload(rewind, profile), {
            "kind": "synthetic", "http": "HTTPX MockTransport",
            "database": "local SQLite :memory:", "redis": "in-memory command fixture",
            "sources": "explicit time and UUID calls",
        }
    module_name, separator, function_name = factory.partition(":")
    if not separator or not module_name or not function_name.isidentifier():
        raise ValueError("factory must be an explicit importable module:callable")
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    workload = function(rewind, dict(profile))
    if not callable(workload) or inspect.isawaitable(workload):
        raise TypeError("factory must return an async callable, not an awaitable factory")
    source = getattr(module, "__file__", None)
    return workload, {
        "kind": "user-selected", "module_path": source,
        "module_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None,
    }


async def close_workload(workload: Any) -> None:
    close = getattr(workload, "aclose", None)
    if close is not None:
        await close()


class TimedStore:
    def __init__(self, path: Path, profile: dict, *, gated: bool = False) -> None:
        self.store = LocalStore(path, max_bytes=profile["store_bytes"])
        self.delay = profile["store_delay_ms"] / 1000
        self.lock = threading.Lock()
        self.arrivals: dict[int, float] = {}
        self.lags: list[float] = []
        self.service: list[float] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not gated:
            self.release.set()

    def save(self, snapshot):
        started = time.perf_counter()
        with self.lock:
            enqueued = self.arrivals.pop(id(snapshot), None)
            if enqueued is not None:
                self.lags.append((started - enqueued) * 1000)
        self.entered.set()
        if not self.release.wait(60):
            raise RuntimeError("validation gate deadline")
        if self.delay:
            time.sleep(self.delay)
        try:
            return self.store.save(snapshot)
        finally:
            with self.lock:
                self.service.append((time.perf_counter() - started) * 1000)


class TimedWriter(BackgroundWriter):
    def __init__(self, store: TimedStore, profile: dict) -> None:
        self.timing = store
        super().__init__(store, max_items=profile["queue_items"], max_bytes=profile["queue_bytes"])

    def submit(self, snapshot) -> bool:
        with self.timing.lock:
            self.timing.arrivals[id(snapshot)] = time.perf_counter()
        accepted = super().submit(snapshot)
        if not accepted:
            with self.timing.lock:
                self.timing.arrivals.pop(id(snapshot), None)
        return accepted


def recorder(mode: str, path: Path, profile: dict, *, gated: bool = False):
    store = TimedStore(path, profile, gated=gated) if mode in {"retained", "background"} else None
    writer = TimedWriter(store, profile) if mode == "background" else None
    rewind = Rewind(
        application="deployment-validation", code_paths=[__file__],
        policy=CapturePolicy.synthetic(),
        retain=Retention(exceptions=False, status_at_least=None,
                         always=mode in {"retained", "background"}),
        enabled=mode != "disabled", store=store if writer is None else None, writer=writer,
    )
    return rewind, writer, store


async def offered_load(invoke, profile: dict, requests: int) -> dict:
    queue: asyncio.Queue = asyncio.Queue(maxsize=profile["backlog_items"])
    latencies, schedule_lags, queue_waits = [], [], []
    errors = completed = rejected = peak_queue = active = peak_active = 0
    start = time.perf_counter()

    async def worker():
        nonlocal errors, completed, active, peak_active
        while True:
            item = await queue.get()
            if item is None:
                queue.task_done()
                return
            index, target, enqueued = item
            actual = time.perf_counter()
            queue_waits.append((actual - enqueued) * 1000)
            active += 1
            peak_active = max(peak_active, active)
            try:
                await invoke(index)
            except Exception:
                errors += 1
            finally:
                active -= 1
                completed += 1
                latencies.append((time.perf_counter() - target) * 1000)
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(profile["concurrency"])]
    try:
        for index in range(requests):
            target = start + index / profile["offered_rate_per_second"]
            await asyncio.sleep(max(0, target - time.perf_counter()))
            enqueued = time.perf_counter()
            schedule_lags.append(max(0, enqueued - target) * 1000)
            try:
                queue.put_nowait((index, target, enqueued))
                peak_queue = max(peak_queue, queue.qsize())
            except asyncio.QueueFull:
                rejected += 1
        await queue.join()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    elapsed = time.perf_counter() - start
    return {
        "offered": requests, "admitted": requests - rejected, "completed": completed,
        "application_errors": errors, "admission_rejections": rejected,
        "offered_rate_per_second": profile["offered_rate_per_second"],
        "scheduled_window_seconds": (requests - 1) / profile["offered_rate_per_second"],
        "elapsed_seconds": elapsed, "completion_rate_per_second": completed / elapsed,
        "latency_ms": quantiles(latencies), "schedule_lag_ms": quantiles(schedule_lags),
        "queue_wait_ms": quantiles(queue_waits), "peak_request_queue_items": peak_queue,
        "peak_active_requests": peak_active,
    }


async def measurement(mode: str, phase: str, profile: dict, factory: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="rewind-validation-") as temporary:
        rewind, writer, store = recorder(mode, Path(temporary) / "store", profile)
        workload, description = load_workload(factory, rewind, profile)

        async def invoke(index):
            return await workload(index) if mode == "direct" else await rewind.run(workload, index)

        try:
            # Warmups use the same adapter workload but no capture scope. Their
            # allocations remain part of this independent process's RSS baseline.
            for index in range(profile["warmup_requests"]):
                await workload(index)
            before = rss_bytes()
            if phase == "memory":
                tracemalloc.start()
            started = time.process_time()
            count = profile["memory_requests"] if phase == "memory" else profile["requests"]
            load = await offered_load(invoke, profile, count)
            drain_start = time.perf_counter()
            shutdown = await rewind.aclose(timeout=profile["drain_timeout_seconds"])
            drain_seconds = time.perf_counter() - drain_start
            peak = tracemalloc.get_traced_memory()[1] if phase == "memory" else None
            if phase == "memory":
                tracemalloc.stop()
            artifacts = list(store.store.path.glob("*.rewind.json")) if store else []
            result = {
                "mode": mode, "phase": phase, "pid": os.getpid(), "workload": description,
                "load": load, "drain_seconds": drain_seconds,
                "process_cpu_seconds": time.process_time() - started,
                "capture": rewind.stats(), "writer": writer.stats() if writer else None,
                "writer_queue_lag_ms": quantiles(store.lags) if store else quantiles([]),
                "store_service_ms": quantiles(store.service) if store else quantiles([]),
                "shutdown": asdict(shutdown), "artifact_count": len(artifacts),
                "artifact_bytes": sum(path.stat().st_size for path in artifacts),
                "rss_before_bytes": before, "rss_peak_bytes": rss_bytes(),
                "tracemalloc_peak_bytes": peak,
            }
            return result
        finally:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            if store:
                store.release.set()
            await rewind.aclose(timeout=profile["drain_timeout_seconds"])
            await close_workload(workload)


async def drill(profile: dict, factory: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="rewind-drill-") as temporary:
        rewind, writer, store = recorder(
            "background", Path(temporary) / "store", profile, gated=True
        )
        workload, description = load_workload(factory, rewind, profile)
        checks = {}
        try:
            baseline = await workload(0)
            rewind.disable()
            disabled = await rewind.run(workload, 0)
            checks["disabled_matches_direct"] = disabled == baseline
            checks["disabled_admits_nothing"] = rewind.stats()["admitted"] == 0
            rewind.enable()
            entered, resume = asyncio.Event(), asyncio.Event()

            async def active_request():
                entered.set()
                await resume.wait()
                return await workload(0)

            task = asyncio.create_task(rewind.run(active_request))
            await entered.wait()
            rewind.disable()
            resume.set()
            checks["active_request_survives_disable"] = await task == baseline
            checks["active_request_retained"] = rewind.stats()["submitted"] == 1
            gated = await asyncio.to_thread(store.entered.wait, 5) if checks[
                "active_request_retained"
            ] else False
            checks["inflight_store_gate_exercised"] = gated
            rewind.enable()
            results_match = True
            for _ in range(profile["queue_items"] + 2):
                results_match = (await rewind.run(workload, 0) == baseline) and results_match
            checks["saturation_preserves_results"] = results_match
            before = writer.stats()
            checks["saturation_rejects_new_captures"] = before["rejected"] > 0
            checks["queue_items_bounded"] = before["peak_pending_items"] <= profile["queue_items"]
            checks["queue_bytes_bounded"] = before["peak_pending_bytes"] <= profile["queue_bytes"]
            rewind.disable()
            admitted = rewind.stats()["admitted"]
            checks["disabled_under_pressure_matches_direct"] = (
                await rewind.run(workload, 0) == baseline
            )
            checks["disable_stops_new_capture"] = rewind.stats()["admitted"] == admitted
            store.release.set()
            drain_start = time.perf_counter()
            flushed = await rewind.aflush(timeout=profile["drain_timeout_seconds"])
            drain_seconds = time.perf_counter() - drain_start
            drained = writer.stats()
            checks["accepted_work_drains"] = (
                flushed and drained["pending_items"] == 0 and drained["pending_bytes"] == 0
                and drained["saved"] == before["submitted"]
                and drained["failed"] == drained["dropped"] == 0
            )
            rewind.enable()
            checks["reenabled_result_matches_direct"] = await rewind.run(workload, 0) == baseline
            checks["reenable_resumes_admission"] = rewind.stats()["admitted"] == admitted + 1
            shutdown = await rewind.aclose(timeout=profile["drain_timeout_seconds"])
            checks["shutdown_finishes"] = shutdown.drained and not shutdown.worker_alive
            closed_count = rewind.stats()["admitted"]
            checks["closed_wrapper_matches_direct"] = await rewind.run(workload, 0) == baseline
            checks["closed_wrapper_admits_nothing"] = rewind.stats()["admitted"] == closed_count
            checks["rollback_direct_matches_baseline"] = await workload(0) == baseline
            checks["active_reservations_released"] = rewind.stats()["active_captures"] == 0
            return {
                "pid": os.getpid(), "workload": description, "checks": checks,
                "before_gate_release": before, "after_first_drain": drained,
                "final_capture": rewind.stats(), "final_writer": writer.stats(),
                "shutdown": asdict(shutdown), "drain_seconds": drain_seconds,
                "rss_peak_bytes": rss_bytes(), "passed": all(checks.values()),
            }
        finally:
            store.release.set()
            await rewind.aclose(timeout=profile["drain_timeout_seconds"])
            await close_workload(workload)


def budget_values(timing: dict, memory: dict) -> dict:
    load = timing["load"]
    capture = timing["capture"]
    return {
        "latency_p95_ms": load["latency_ms"]["p95"],
        "schedule_lag_p95_ms": load["schedule_lag_ms"]["p95"],
        "queue_wait_p95_ms": load["queue_wait_ms"]["p95"],
        "admission_rejection_fraction": max(
            report["load"]["admission_rejections"] / report["load"]["offered"]
            for report in (timing, memory)
        ),
        "application_error_fraction": max(
            report["load"]["application_errors"] / max(1, report["load"]["completed"])
            for report in (timing, memory)
        ),
        "capture_rejection_fraction": max(
            state["enqueue_rejected"] / max(1, state["retained"])
            for state in (capture, memory["capture"])
        ),
        "rss_peak_bytes": memory["rss_peak_bytes"],
        "tracemalloc_peak_bytes": memory["tracemalloc_peak_bytes"],
        "drain_seconds": timing["drain_seconds"],
        "writer_queue_lag_p95_ms": (
            timing["writer_queue_lag_ms"]["p95"] if timing["writer"] is not None else 0
        ),
    }


def evaluate(actual: dict, limits: dict) -> dict:
    return {
        name: {"actual": actual.get(name), "maximum": maximum,
               "passed": type(actual.get(name)) in (int, float)
               and math.isfinite(actual[name]) and actual[name] <= maximum}
        for name, maximum in limits.items()
    }


def run_child(mode: str, phase: str, profile: dict, factory: str) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", mode, "--phase", phase,
               "--factory", factory]
    try:
        result = subprocess.run(
            command, input=json.dumps(profile), capture_output=True, text=True,
            timeout=profile["process_timeout_seconds"],
        )
        if result.returncode:
            return {"error": "worker_failed", "returncode": result.returncode}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "worker_deadline_exceeded"}
    except (OSError, ValueError):
        return {"error": "worker_unavailable_or_invalid_report"}


def validate_deployment(profile: dict, factory: str) -> dict:
    result = {
        "schema_version": 1, "scope": "local staging validation, not production certification",
        "provenance": provenance(factory), "profile": profile,
        "profile_sha256": hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest(),
        "modes": {}, "passed": True,
    }
    for mode in profile["modes"]:
        timing = run_child(mode, "timing", profile, factory)
        memory = run_child(mode, "memory", profile, factory)
        entry = {"timing": timing, "memory": memory, "passed": False}
        if "error" not in timing and "error" not in memory:
            limits = profile["budgets"]["default"] | profile["budgets"]["modes"].get(mode, {})
            checks = evaluate(budget_values(timing, memory), limits)
            invariants = {}
            for phase, report in (("timing", timing), ("memory", memory)):
                load, capture, writer = report["load"], report["capture"], report["writer"]
                invariants[f"{phase}_requests_accounted"] = (
                    load["completed"] + load["admission_rejections"] == load["offered"]
                    and load["admitted"] == load["completed"]
                )
                invariants[f"{phase}_no_storage_failure"] = capture["persistence_failed"] == 0
                invariants[f"{phase}_reservations_released"] = capture["active_captures"] == 0
                invariants[f"{phase}_persisted_artifacts_available"] = (
                    capture["persisted"] == report["artifact_count"]
                )
                invariants[f"{phase}_shutdown_drained"] = (
                    report["shutdown"]["drained"] and not report["shutdown"]["worker_alive"]
                )
                if writer:
                    invariants[f"{phase}_queue_bounded"] = (
                        writer["peak_pending_items"] <= profile["queue_items"]
                        and writer["peak_pending_bytes"] <= profile["queue_bytes"]
                    )
                    invariants[f"{phase}_accepted_saved"] = writer["submitted"] == writer["saved"]
            entry.update(checks=checks, invariants=invariants)
            entry["passed"] = all(check["passed"] for check in checks.values()) and all(
                invariants.values()
            )
        result["modes"][mode] = entry
        result["passed"] = result["passed"] and entry["passed"]
    exercise = run_child("drill", "timing", profile, factory)
    if "error" not in exercise:
        checks = evaluate(exercise, profile["budgets"]["drill"])
        exercise["budgets"] = checks
        exercise["passed"] = exercise["passed"] and all(item["passed"] for item in checks.values())
    else:
        exercise["passed"] = False
    result["drill"] = exercise
    result["passed"] = result["passed"] and exercise["passed"]
    result["finished_at_utc"] = datetime.now(UTC).isoformat()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--factory", default="builtin:synthetic")
    parser.add_argument("--worker", choices=(*MODES, "drill"), help=argparse.SUPPRESS)
    parser.add_argument("--phase", choices=("timing", "memory"), default="timing",
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read(65_537) if args.worker else (
            args.profile.read_text() if args.profile is not None else ""
        )
        profile = read_profile(raw)
        if args.worker:
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), \
                    contextlib.redirect_stderr(sink):
                report = asyncio.run(drill(profile, args.factory) if args.worker == "drill" else
                                     measurement(args.worker, args.phase, profile, args.factory))
        else:
            report = validate_deployment(profile, args.factory)
        encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(encoded)
        else:
            print(encoded, end="")
        return 0 if args.worker or report["passed"] else 1
    except Exception as exc:
        # Custom workload exceptions may contain credentials: expose a type only.
        parser.exit(2, f"deployment validation could not complete ({type(exc).__name__})\n")


if __name__ == "__main__":
    raise SystemExit(main())
