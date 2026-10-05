# Bounded background persistence

`BackgroundWriter` moves snapshot storage calls onto a single daemon thread.
The request still creates, redacts, encodes, and seals its snapshot before
submission. Only an immutable `Snapshot` containing `bytes` crosses the queue;
live request objects, recorder contexts, and mutable buffers are rejected.

```python
from rewind import LocalStore
from rewind.persistence import BackgroundWriter

writer = BackgroundWriter(
    LocalStore(".rewind/snapshots"),
    max_items=128,
    max_bytes=16 * 1024 * 1024,
)

# snapshot is an already sealed Snapshot from capture.
accepted = writer.submit(snapshot)
completed = writer.flush(timeout=5)
report = writer.close(timeout=5, drain=True)
```

The `SnapshotStore` protocol requires only `save(snapshot: Snapshot) -> object`.
Stores perform their own schema validation and storage policy enforcement;
`LocalStore` validates again before writing. Directly constructing a `Snapshot`
does not validate its JSON. Use `Snapshot.from_bytes` or `Snapshot.from_dict`
when importing data.

## Admission and bounds

Both queue limits include the currently executing save. Submission returns
`False` when either limit would be exceeded, after shutdown, for a mutable or
incorrectly typed payload, or in a process forked from the writer's owner.
Submission never performs disk I/O or waits for capacity. It takes a brief
bookkeeping lock; the store never runs under that lock. Existing queued work
is preserved when a new submission is rejected.

The byte budget counts serialized snapshot bytes. Queue entries, Python
objects, thread stacks, temporary sealing allocations, and store internals
consume additional memory. A blocked save continues to occupy its budget
until it returns. Configure limits for the application's expected payloads.
Queue capacities must be positive integers; booleans are rejected.

## Flush and shutdown

`flush(timeout=5)` waits until the writer is idle. Submissions accepted while
it waits are also included; it is not a fixed submission barrier. It returns
`False` at the deadline and leaves the queue unchanged. Successful completion
does not imply every write succeeded: inspect `failed` as well as `saved`.

`close(timeout=5, drain=True)` first rejects further submissions, then waits
up to the deadline. It discards work still queued at that deadline. With
`drain=False`, queued work is discarded immediately; the method may still wait
for the active save until its deadline. A running save cannot be cancelled.
Timeout zero requests an immediate state report. Repeated calls are safe and
can observe a formerly blocked save completing later. Timeouts must be finite,
nonnegative numbers no larger than `threading.TIMEOUT_MAX`.

The frozen `ShutdownReport` describes state at return:

| Field | Meaning |
| --- | --- |
| `drained` | No accepted work remains, and none was discarded during shutdown. Writes may still have failed. |
| `pending` | Accepted items still owned by the writer, including an active save. |
| `in_flight` | A store save is still executing. |
| `dropped` | Cumulative accepted items discarded during shutdown. |
| `worker_alive` | The worker thread is still alive. |
| `forked` | This process inherited the writer and cannot operate it. |

Call shutdown from application lifecycle code. A daemon worker does not keep
the process alive, so process exit can lose queued or active writes. An
uninterruptible store can outlive the deadline; the report exposes this state.
For a new forked worker process, create a new writer after the fork. Inherited
writer methods return immediately without acquiring inherited locks or joining
the parent's thread. Child statistics are zeroed and marked `forked=True`;
they do not describe the parent process.

## Observability and failures

`stats()` returns a detached dictionary with a fixed set of integer and boolean
values. No snapshot IDs, payloads, file paths, exception messages, or traceback
objects are retained as metrics.

| Metric | Meaning |
| --- | --- |
| `submitted` | Accepted submissions. |
| `saved` | Saves whose store call returned successfully. |
| `failed` | Store calls that raised, including thread-local termination exceptions. |
| `dropped` | Accepted queued items discarded at shutdown. |
| `rejected` | Submissions rejected for capacity, shutdown, or payload type. |
| `pending_items`, `pending_bytes` | Current ownership, including the active save. |
| `peak_pending_items`, `peak_pending_bytes` | Highest observed ownership. |
| `max_items`, `max_bytes` | Configured limits. |
| `in_flight`, `worker_alive`, `closed`, `forked` | Current lifecycle flags. |

Cumulative counters saturate at `2**63 - 1`. Before saturation, in the owning
process, `submitted = saved + failed + dropped + pending_items`. A failed
save is not retried; later submissions can still succeed. This avoids
unbounded retries and duplicate side effects in custom stores. The worker
starts with an empty context, so active capture and other request ContextVars
are not inherited. The store remains responsible for durable atomic writes,
filesystem access controls, and its own internal resource limits.
