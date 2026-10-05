# Local staging validation and rollback drill

This tool turns a stated workload and numeric acceptance budgets into a repeatable
pass/fail report. The bundled profile is a **synthetic local staging exercise**.
Its example budgets are not production SLOs, customer workload measurements, or
approval to capture sensitive data.

Run from a checkout with the package and HTTPX installed:

```sh
python scripts/validate_deployment.py \
  --profile docs/validation/local-staging.json \
  --output /tmp/rewind-deployment-validation.json
```

The fixture executes actual Rewind adapters: HTTPX with a `MockTransport`, local
SQLite through `RecordingSQLite`, a Redis wrapper over a small in-memory command
fixture, and explicit time/UUID observations. It performs no external network
requests. The HTTP and Redis services are simulated; this does not measure a
running database server, Redis server, or a real HTTP service. Each operation
performs one SQLite query, one Redis read, one HTTP request, and a cooperative
wait, returning a deterministic projection of its application result.

The supplied profile offers 100 requests at 50 requests/second, with eight
workers, a 32-item request backlog, a 16-item/2 MiB persistence queue, 256-byte
payloads, and a 2 ms cooperative wait. Its independent memory pass offers 20
requests. Temporary artifacts stay in private temporary directories and are
removed after each child process's measurement.

## Define acceptance before measuring

Copy the profile and choose limits appropriate to the intended staging workload
before running it. Keep the original profile and failed reports when investigating
a failure. Changing limits is a new experiment, not evidence that a prior run
passed. The bundled thresholds are deliberately identified as examples:

| Numeric maximum | Example budget |
| --- | --- |
| End-to-end accepted-request p95 | 200 ms |
| Offered-request scheduling lag p95 | 50 ms |
| Request queue wait p95 | 100 ms |
| Request admission rejection fraction | 0 |
| Application error fraction | 0 |
| Capture enqueue rejection fraction | 0.05 |
| Independent memory-process RSS peak | 256 MiB |
| Traced Python allocation peak | 64 MiB |
| Lifecycle drain duration | 10 seconds |
| Writer submission-to-save-start p95 | 100 ms |

`budgets.default` must include every documented metric. `budgets.modes` can
override individual maxima for selected modes. `budgets.drill` has separate
`drain_seconds` and `rss_peak_bytes` maxima. Missing or unavailable measurements
fail their numeric gate; they never silently pass. RSS is supported on Linux and
macOS; another platform reports it unavailable and fails the RSS gate.

The tool also checks non-negotiable invariants: request accounting, released
capture reservations, successful writer shutdown, no persistence failures,
availability of the persisted artifacts, bounded writer queue peaks, and accepted
background submissions being saved. A storage quota that evicts artifacts during
the run fails artifact availability even if each individual save returned normally.

Exit `0` means all selected mode budgets, invariants, and drill checks passed.
Exit `1` means completed evidence contains a failure, including a child deadline.
Exit `2` means the invocation/configuration could not produce a report. Reports
retain failed checks with actual and maximum values. Application exception text
and response payloads are not included in the report.

## Workload and measurement boundaries

| Mode | Execution |
| --- | --- |
| `direct` | Invoke the application workload without a capture scope. |
| `disabled` | Invoke through a disabled Rewind instance. |
| `active` | Capture with retention configured to discard all observations. |
| `retained` | Persist each captured execution synchronously. |
| `background` | Attempt bounded background persistence and drain afterward. |

The load generator schedules request `i` at `start + i / offered_rate_per_second`.
It continues offering work independently of request completion. A full application
backlog rejects new requests immediately. This is distinct from the persistence
queue rejecting a completed request's snapshot; both counts are reported separately.
If application code blocks the event loop, overdue offers are attempted when the
generator resumes. `schedule_lag_ms` exposes that delay instead of quietly lowering
the stated offered rate. This is a same-process load generator, not an external
network traffic generator or a model of kernel/server accept queues.

`latency_ms` begins at the intended arrival time and ends at application completion
for admitted requests. It includes scheduling lag, backlog wait, and execution.
`queue_wait_ms` starts when the request is admitted to the application backlog.
Scheduling samples include rejected offers; completion samples do not. Read
rejection counts alongside latency, especially when permitting nonzero rejection.
Quantiles use linear interpolation; the report includes completion sample counts.

Each mode's timing pass and memory pass run in **separate fresh interpreters**.
Timing passes do not enable `tracemalloc`. Memory passes enable it after workload
setup/warmup and continue through workload and writer drain. Its peak includes
harness samples and tracking structures; it excludes setup and native allocations.
RSS is that child process's lifetime high-water mark, including imports and setup.
`rss_before_bytes` is the high-water mark after warmup, not instantaneous RSS.
These independent absolute peaks avoid contamination from previously measured modes;
they are observed memory use, not a mathematical process-memory bound.

The writer meter records elapsed time from just before `submit()` until the store's
`save()` begins. It includes the small admission/dispatch cost as well as queue
waiting. A separate service-time measurement covers the store call, optional
synthetic storage delay, and filesystem publication. Instrumentation adds cost.
`store_delay_ms` defaults to zero and can model slow persistence explicitly.

There is a finite parent-enforced `process_timeout_seconds` for every child. A
hung application cannot leave the coordinator waiting indefinitely. Measurements
from a killed child are unavailable and fail acceptance. The profile allows at
most 100,000 requests per pass and 1,024 workers/queue slots for persistence; these
are tooling limits, not recommended deployment settings. A mode can still exceed
the capacity of the machine or its intended latency budget within those bounds.

## Enable, disable, drain, and rollback

The drill holds the first persistence save behind an event-controlled gate and:

1. Compares direct execution with capture disabled; no capture is admitted.
2. Enables capture, admits a request, disables capture while that request is active,
   and verifies it finishes with its original result and retains its artifact.
3. Enables capture and fills the persistence budget while the store is blocked.
   New snapshots are rejected; original application results remain unchanged.
4. Disables capture under pressure, verifies new calls remain functional without
   admission, releases the store, and drains accepted work.
5. Enables capture again and verifies admission resumes.
6. Closes capture, verifies the worker stops and reservations are released, then
   compares both the closed wrapper and direct application calls with the baseline.

The direct rollback removes the capture entry-point wrapper. Dependency wrappers
remain installed and follow their normal pass-through behavior outside a capture
scope. The drill does not change a deployed service's configuration or roll back
a release. It is an executable lifecycle exercise on a dedicated local instance.
If one artifact cannot fit in the queue, the report fails the in-flight exercise
instead of claiming that a blocked-write scenario was tested.

## Supply your own staging application

Factories are selected only by the explicit CLI option, never by profile or
snapshot contents:

```sh
PYTHONPATH=. python scripts/validate_deployment.py \
  --profile my-staging-profile.json \
  --factory my_staging_workload:build \
  --output /tmp/my-staging-report.json
```

The module must be importable in the fresh child processes. `build(rewind,
profile)` returns an async callable accepting a request index. It may expose
`async aclose()` for cleanup. Build application dependencies using the supplied
Rewind instance's supported adapters. The harness owns the capture entry point:
it calls your workload directly or through `rewind.run()` according to the mode.
Do not add another unconditional capture decorator inside the workload.

```python
def build(rewind, profile):
    async def request(index):
        observation = rewind.sources.uuid4()
        assert observation
        # Call the staging application using its supported adapters here.
        return {"request_index": index, "business_status": "accepted"}
    return request
```

For rollback comparison, repeated index `0` must return an equivalent deterministic
business projection. Observe clock/ID values normally but omit intentional changing
values from this projection. Factories are trusted local application code and can
perform side effects: use isolated staging data and resources. Credentials belong
in the application's normal secret configuration, not in a committed profile.
Profiles contain only the documented numeric settings, mode selection, budgets,
and a descriptive name. Unrecognized fields, duplicate JSON keys, non-finite
numbers, and invalid limits are rejected before importing a factory.

## Evidence provenance

Reports include UTC start/end, exact profile and hash, Git revision/dirty state,
script hash, imported Rewind source-tree hash/path, package and dependency versions,
Python executable/version, platform, factory identity, custom factory module hash,
and per-child PIDs. Installed distribution metadata can differ from an edited
checkout; the source hash makes that distinction reviewable. Preserve profiles,
reports, source revisions, and relevant machine/service load conditions together.

This evidence covers the chosen staging workload and lifecycle drill. External
data-policy approval, production SLO ownership, crash/power-loss behavior, and
representative customer traffic remain separate deployment decisions.
