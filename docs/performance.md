# Capture performance and failure-storm validation

Run the synthetic benchmark from a checkout with the package installed:

```sh
python scripts/benchmark_capture.py --output /tmp/rewind-benchmark.json
```

The default uses 100 measured operations, 10 warmup operations, three repeats,
four async workers, 256-byte payloads, and separate 20-operation memory passes.
Temporary artifacts are deleted after each trial. The script needs only Python's
standard library and Rewind. It does not call a network service.

For a longer local sample with explicit settings:

```sh
python scripts/benchmark_capture.py \
  --iterations 500 --warmup 50 --repeats 5 --concurrency 8 \
  --payload-bytes 1024 --queue-items 64 --queue-bytes 1048576 \
  --io-delay 0.001 --memory-iterations 100 \
  --output /tmp/rewind-benchmark.json
```

## Modes and workloads

| Mode | Behavior |
| --- | --- |
| `baseline` | Calls the workload directly. |
| `disabled` | Calls through a disabled Rewind instance. |
| `discarded` | Records a successful call; retention discards the result. |
| `synchronous` | Retains every call and waits for LocalStore publication. |
| `background` | Retains every call, attempts bounded writer admission, then drains after the measured workload. |
| `storm` | Records repeated failures while the first store write is held behind a controlled gate, then drains and checks accounting. |

Use `--modes baseline,disabled,discarded` to select a subset, or
`--modes storm --queue-items 1` to focus on capacity including an in-flight write.
Background overload is an expected result: rejected admissions appear in the
report, and throughput must be read alongside the number actually saved.
The `completion_counts` beside each mode's timing summary explicitly totals
measured operations, persisted artifacts, rejected writer admissions, and storage
failures. Different persisted counts make raw throughput comparisons inequivalent.

The CPU fixture performs a fixed small arithmetic calculation. The I/O fixture
performs that same calculation and an `asyncio.sleep` with `--io-delay` seconds.
The delay models cooperative waiting; it does not model HTTP transport, a real
database, or their adapters. The ASCII payload length is exactly
`--payload-bytes`; serialized artifacts also contain metadata and encoding overhead.
Workers issue their next call after the previous one completes: this is closed-loop
concurrency, not a fixed-arrival-rate workload or a measurement of external queueing.

## Reading the JSON report

Each mode has raw trial measurements and a `median` of corresponding trial
statistics. `latency_us` reports interpolated per-operation p50, p95, and p99.
The median p95 is the median of trial p95 values, not the p95 of pooled samples.
Latency starts when a worker begins an operation; it includes async waiting and
event-loop contention, but not time waiting for a worker to be assigned work.

`throughput_ops_per_second` measures workload completions before writer drain.
`including_drain_ops_per_second` includes drain time. `process_cpu_seconds`
counts CPU for the process, including the writer thread during the workload;
it excludes the subsequent drain phase. `including_drain_process_cpu_seconds`
includes the final drain CPU work as well. Drain wall time is reported separately.
Artifact counts and bytes are collected after drain. `writer_stats` distinguishes
admissions, successful disk writes, failures, and capacity rejection.

`delta_from_baseline` compares the medians for the same workload. Absolute latency
deltas are microseconds; relative deltas are percentages. Relative deltas are
`null` when the baseline is zero. A positive latency delta means more time; a
positive throughput delta means more operations per second. No pass/fail test
depends on a speedup, percentage target, or a particular machine's timing.

The separate `memory_pass` enables `tracemalloc` after recorder/store setup and
measures peak traced Python allocation through drain. It includes harness sample
storage and writer bookkeeping. It excludes setup allocations, native allocations,
kernel caches, and any discarded warmup allocations. Its timings are intentionally
excluded from reported latency comparisons. Set `--memory-iterations 0` to skip it.
On Linux and macOS, `rss_high_water` converts the platform's original units to
bytes. This is the lifetime high-water mark of the entire benchmark process,
shared across modes, and cannot attribute memory growth to a single mode.

Every mode gets a discarded warmup trial, followed by fresh recorder/store instances
for measured trials. Filesystem and interpreter caches remain warm across trials.
Modes run in the requested order, not random order. Repeat with reversed ordering
to investigate cache, thermal, or competing-process effects. The report records
Python, platform, installed package versions, and the exact settings; source
revision and machine load should accompany any published report. Installed package
metadata can differ from an edited checkout, so retain the Git revision as well.

## Controlled failure storm

The gate holds the first accepted disk write while subsequent captures compete
for the item/byte limits. At least `queue-items + 2` failures are attempted so
capacity rejection is exercised. The snapshot remains charged while in flight.
The script verifies budget bounds, submitted/rejected accounting, retained byte
reservations, observed rejection, successful draining, and saved artifact counts.
Writer `rejected` means an attempted admission was refused; `dropped` means an
already accepted queued item was discarded during shutdown. This scenario expects
rejection and no shutdown drops. Queue shutdown-drop behavior has separate unit tests.

A byte budget smaller than one artifact rejects all captures. The report sets
`in_flight_exercised` to false in that case rather than claiming an in-flight test.
The gate has a 60-second failsafe; `--drain-timeout` defaults to 30 seconds.
Use reasonable queue/payload sizes: these are explicit experiment controls, not
an instruction to exhaust available RAM or disk.

Exit status is `0` for completed measurements with passing storm invariants,
`1` for a failed storm invariant, and `2` for invalid arguments or execution errors.
JSON serialization rejects non-finite numbers. The toolkit provides repeatable
evidence for synthetic workloads; it does not establish production latency,
cross-process storage quotas, power-loss durability, or a universal overhead claim.
