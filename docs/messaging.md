# Kafka and Celery

These explicit adapters record the queue operations that application code observes.
Replay consumes those observations in order, without creating a producer, consumer,
Celery application, broker connection, or worker. Dependencies still need to be
installed because replay reconstructs their public result types.

This feature is part of the development version 0.2.0a1. From a checkout containing
this change, install the relevant extras:

```bash
python -m pip install -e '.[kafka,celery]'
```

The supported client families are `kafka-python>=2.2,<3` and `celery>=5.5,<6`.
Conformance was exercised with kafka-python 2.3.2, Apache Kafka 3.9.1 and Celery
5.6.3. This is a bounded API contract, not a claim about every broker deployment
or distributed scheduling behavior.

## Kafka

Create and configure native clients outside the captured entrypoint. Wrap them for
capture; construct the same wrappers without native clients in the replay factory.
Use stable dependency names to distinguish clients.

```python
from kafka import KafkaProducer
from rewind.adapters.kafka import RecordingKafkaProducer

native = KafkaProducer(bootstrap_servers=["127.0.0.1:9092"])
producer = RecordingKafkaProducer(native, dependency="orders.producer")

def operation():
    future = producer.send("orders", value=b'{"order_id":7}', key=b"7")
    metadata = future.get(timeout=5)
    return metadata.topic, metadata.partition, metadata.offset

# In the replay factory, use:
# producer = RecordingKafkaProducer(dependency="orders.producer")
```

Call `operation` through `Rewind.run_sync`, or call the synchronous wrapper methods
inside a captured asynchronous entrypoint. These methods remain synchronous and
can block during capture. `send` returns immediately when the native `send` does;
the adapter does not wait for acknowledgement until application code calls `get`.
Replay preserves the captured metadata and any supported timeout exception.
See kafka-python's [producer future contract](https://kafka-python.readthedocs.io/en/latest/apidoc/misc/FutureRecordMetadata.html).

| Wrapper | Supported observations |
| --- | --- |
| `RecordingKafkaProducer` | `send`, `flush`, `close` |
| Returned producer future | `get(timeout=...)` |
| `RecordingKafkaConsumer` | `poll(timeout_ms=..., max_records=..., update_offsets=...)`, `close(autocommit=...)` |

`poll` preserves dictionaries keyed by `TopicPartition`, lists of `ConsumerRecord`,
their offsets, timestamps, headers, and supported decoded key/value types. Producer
`get` reconstructs `RecordMetadata` with its `TopicPartition`. Empty polls are
observations too: replay preserves their position in the sequence without waiting.
Configure subscriptions, assignment and deserializers before wrapping the native
consumer. Their configuration and implementation are application responsibilities;
include relevant source/configuration in your fingerprint inputs.

Consumer iteration, subscription changes, explicit offset commits, rebalances,
transactions and producer callbacks are outside this contract. Public unsupported
attribute access delegates during capture and marks the recording incomplete;
replay fails before delegating. Special Python protocols, such as iteration and
context-manager use, are not provided by these wrappers. Wrappers are not native
client/future instances, so native `isinstance` checks are not supported.

Kafka exception reconstruction is limited to the exact classes
`KafkaTimeoutError`, `KafkaConnectionError`, `NoBrokersAvailable`,
`UnknownTopicOrPartitionError`, `RequestTimedOutError`, `MessageSizeTooLargeError`,
`KafkaConfigurationError`, and built-in `ValueError`, `TypeError`, `AssertionError`.
Other exception types still propagate unchanged during capture and make the
recording incomplete.

## Celery

`RecordingCelery` wraps an existing configured application. Its `task(name)` helper
selects an already registered task; it is not Celery's task-registration decorator.

```python
from rewind.adapters.celery import RecordingCelery

tasks = RecordingCelery(app, dependency="orders.tasks")

def operation():
    result = tasks.task("orders.confirm").apply_async(args=(7,), queue="orders")
    identifier = result.id
    return identifier, result.get(timeout=10), result.status

# Replay factory:
# tasks = RecordingCelery(dependency="orders.tasks")
```

Supported submissions are `send_task(name, args=..., kwargs=..., **options)`,
`task(name).apply_async(...)`, and `task(name).delay(...)`. Supported submission
options are `task_id`, `countdown`, `eta`, `expires`, `queue`, `routing_key`,
`exchange`, `priority`, `serializer`, `compression`, `time_limit`, `soft_time_limit`,
`headers`, `ignore_result`, `retry`, `retry_policy`, and `delivery_mode`. The adapter
records their values; replay does not schedule or retry work.

The returned result proxy supports `id`, `state`, `status`, `result`, `get`,
`ready`, `successful`, `failed`, and `forget`. The task ID is recorded at submission.
Each subsequent state/result/backend read is a separate ordered observation. `get`
supports `timeout`, `propagate`, `interval`, `no_ack`, `follow_parents`, and
`disable_sync_subtasks`; callbacks and additional keyword options make capture
incomplete. `forget` runs against the backend during capture only.

Eager `apply_async` and `delay` execute the actual task during capture. The adapter
suspends the producer's capture context around submission, so an eager task body
does not become part of the producer's interaction stream. Replay uses the
recorded result and never executes that task. Celery's `send_task` does not honor
`task_always_eager`; use a worker and broker for its live execution. See the
[eager configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-always-eager)
and [task submission API](https://docs.celeryq.dev/en/stable/reference/celery.app.task.html).

Celery reconstructs exact `celery.exceptions.TimeoutError`, `TaskRevokedError`,
`kombu.exceptions.OperationalError`, and built-in `ValueError`, `TypeError`,
`KeyError`, `IndexError`, `ZeroDivisionError`, `RuntimeError`. This also covers
exception objects returned by `get(propagate=False)` or `result`. Unknown task
exceptions preserve the live behavior but make capture incomplete. Exception
arguments must also pass the capture policy and typed codec limits.

Chains, groups, chords, callbacks, result graphs, custom result classes and worker
execution/scheduling replay are unsupported. Unsupported public operations and
options delegate during capture while marking the recording incomplete; replay
never delegates them. Capture does not promise to reproduce side effects performed
inside a remote or eager task body.

## Runnable synthetic example

The example uses a real Celery eager task and requires no broker service. Its task
returns a pending order without `confirmation_id`, producing a captured `KeyError`.
The replay factory creates no Celery application.

```bash
python -m examples.messaging_failure --store .rewind/messaging-demo
rewind replay .rewind/messaging-demo/<printed-id>.rewind.json \
  --app examples.messaging_failure:replay_target
```

Use the exact path printed by the first command. The expected report is
`reproduced`. The example deliberately uses `CapturePolicy.synthetic()` with
synthetic values; it is not a production data policy.

## Privacy, ownership and completeness

All events use `messaging.call` with a stable dependency name, adapter, target,
method and typed parameters. Required observations share the usual interaction
and artifact bounds. Capture failures do not replace the application's return
value or exception. Exclusions, unsupported values, budget exhaustion and use
from a copied child-task/thread context make the artifact incomplete.

The default capture policy excludes values needed for exact replay. Explicitly
choose a policy appropriate for the data. Named sensitive fields in structured
messages, Celery arguments/results and submission headers follow `CapturePolicy`,
including custom `redacted_keys`. Kafka header pairs are checked by name before
capture, including binary header values. Kafka key/value bytes require
`capture_binary`; JSON-looking text/bytes are inspected for sensitive structured
fields, and the whole raw value is excluded if that policy removes a field.
Malformed JSON-looking messages are also excluded. Opaque text/binary values
cannot be reliably classified by field name: do not enable raw capture for
unreviewed secrets. Client configuration, connection credentials and task
tracebacks are not recorded by these adapters.

Futures and result handles belong to the capture that created them. Accessing a
handle from another capture, a child task or another thread cannot produce a
complete recording. A changed method, dependency, argument, read order or outcome
shape causes replay to diverge; there is no broker/backend fallback.

## Conformance checks

```bash
python -m pytest tests/test_kafka.py tests/test_celery.py -q
```

The default run exercises real kafka-python future/result classes, actual Celery
eager tasks and a Celery worker using the in-memory Kombu broker/result backend.
The Kafka broker case skips unless `REWIND_KAFKA_BOOTSTRAP` is set. Run it against
a disposable broker with topic auto-creation enabled:

```bash
REWIND_KAFKA_BOOTSTRAP=127.0.0.1:59092 \
  python -m pytest tests/test_kafka.py tests/test_celery.py -q
```

The Kafka case creates a unique synthetic topic, sends a record, polls it, closes
the native clients, and replays without them. The Celery worker case stops its
worker before replay and verifies the task executed only once. Kafka 3.9.1's
[official Docker guide](https://kafka.apache.org/39/getting-started/docker/)
describes the disposable image used for broker conformance.
