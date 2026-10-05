# Capture operations

[Documentation home](index.md) · [Quick start](getting-started.md)

Rewind can move artifact filesystem writes off the request event loop using a
bounded `BackgroundWriter`. Serialization, policy filtering, and fingerprinting
remain in process; capture still has CPU and memory costs. This is a local alpha,
not a durable production telemetry pipeline.

```python
from rewind import BackgroundWriter, CapturePolicy, LocalStore, Rewind

store = LocalStore(".rewind/captures")
writer = BackgroundWriter(store, max_items=128, max_bytes=16 * 1024 * 1024)
rewind = Rewind(
    application="checkout", code_paths=[__file__], writer=writer,
    policy=CapturePolicy(),
)

# Use await rewind.run(handler, ...) or rewind.asgi(app) as before.
# When stopping: stop new requests, await application tasks, then close.
report = await rewind.aclose(timeout=5, drain=True)
print(rewind.stats())
```

`writer=` and `store=` are mutually exclusive. Existing `store=` capture remains
synchronous: the application waits for each retained artifact write. The default
capture policy omits sensitive payloads and produces diagnostic recordings;
`CapturePolicy.synthetic()` is intended for controlled synthetic inputs when
replay needs complete values. Both persistence paths sanitize and seal immutable
snapshot bytes before storage receives them. The worker does not receive live
request objects or application exceptions.

## Queue behavior and accounting

An accepted queue submission is **not a persisted artifact**. Capacity includes
queued and currently writing artifacts. Saturation rejects new artifacts without
waiting for filesystem I/O; the original application result, error, cancellation,
and ASGI response continue normally. Storage failure also does not alter the
application outcome. Captured payload references are released when finalization
finishes, including when a queue rejects a recording.

`rewind.stats()` returns a separate dictionary with fixed, payload-free keys:

| Keys | Meaning |
| --- | --- |
| `admitted`, `admission_rejected` | Started captures and active-memory budget rejections |
| `active_captures`, `reserved_bytes` | Live captures and their conservative snapshot-size reservations |
| `retained`, `incomplete` | Sealed recordings selected by retention, and selected recordings ineligible for replay |
| `submitted`, `enqueue_rejected` | Accepted writer submissions and rejected submissions |
| `persisted`, `persistence_failed` | Completed successful writes and capture finalization or write failures |
| `pending_items`, `pending_bytes` | Artifacts owned by the writer, including its current write |
| `closed_rejected` | Active captures finalized after capture shutdown began |
| `dropped` | Accepted queued artifacts discarded by writer shutdown |
| `enabled`, `closed` | Current admission and terminal lifecycle state |

Counters have no request IDs, URLs, payload fields, or exception messages as labels.
`metrics` remains a `Counter` view for existing readers; it is now a safe copy,
so mutations do not change live statistics. Dedicate a writer to each `Rewind`
instance if these counters must describe that application alone: writer completion
and drop counters cover the writer's entire lifetime, including direct submissions.
Snapshots of statistics do not promise a globally atomic view of independent worker
completion and application execution.

Active memory reservations bound capture admission separately from queue capacity.
They are not measured process RSS and do not include application allocations,
Python object overhead, or temporary serialization allocations.

## Disable, drain, and shutdown

- `disable()` immediately prevents new captures. Already admitted captures finish
  and can persist normally. Application execution continues. `enable()` resumes
  capture; assigning `enabled` remains supported.
- `flush(timeout=5)` waits for writer idle without closing admission.
  `aflush()` performs that bounded wait in a thread. A busy stream of new captures
  can prevent idle until the timeout. Flush does not await active application tasks.
- `close(timeout=5, drain=True)` is terminal: it stops capture admission immediately
  and asks the writer to finish accepted artifacts within the timeout. `aclose()`
  performs the wait off the event loop. Synchronous methods can block their caller;
  use the async forms in an ASGI lifespan shutdown handler.
- `drain=False` discards queued artifacts. An already running filesystem write
  cannot be interrupted. A timeout can return with a daemon worker still alive;
  later completion remains visible in `stats()`. A repeated close can wait again.
- A shutdown report describes the **writer**, not outstanding requests. Rewind
  does not cancel, wait for, or finish application tasks. A capture that finishes
  after close begins releases its payload and increments `closed_rejected`.
  Stop accepting application requests and await them **before** closing to include
  their artifacts. A synchronous store write already in progress may still finish.
- `drained=True` means accepted queue work completed without being discarded, not
  that every write succeeded. Inspect `persistence_failed` as well. `enable()` after
  shutdown raises `RuntimeError`; create a new writer and capture instance instead.

Cancelling `aclose()` does not cancel dispatch of writer shutdown, even when the
executor is occupied; shutdown still runs when a worker becomes available.
Cancelling `aflush()` can cancel a wait that has not started; an already running
thread wait continues. In both async methods the timeout bounds the writer wait
after executor dispatch, not time spent waiting for an available executor thread
or event-loop scheduling. They are not hard wall-clock deadlines. Process exit
can lose queued artifacts. There is no WAL,
retry, network exporter, multiprocess queue, or hard filesystem timeout. Use a new
writer after forking, and construct the associated `Rewind` instance in that child;
its own lifecycle lock is intended for threads in the constructing process.

## Rollback and runnable demonstration

Disable admission to remove ongoing capture work quickly, then drain and close
once requests have finished. For synchronous persistence, construct a fresh
`Rewind(store=store, ...)` without `writer=`. No snapshot schema migration is needed;
artifacts remain readable by the same CLI and replay APIs.

```sh
python -m examples.background_capture --store .rewind/background-demo
```

The example records a synthetic failure, flushes its artifact, closes the writer,
and replays in a fresh process. It prints counters and the replay report. See
[performance measurements](performance.md) for the benchmark harness and its limits.
