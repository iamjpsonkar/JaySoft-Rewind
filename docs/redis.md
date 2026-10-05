# Redis capture and replay

Install `pip install 'jaysoft-rewind[redis]'`. The adapter targets redis-py
`>=5,<7`; conformance is exercised with redis-py 6.4.0 against a real Redis server.
Use `RecordingRedis` for `redis.Redis` and `AsyncRecordingRedis` for
`redis.asyncio.Redis`. Client configuration stays on your original client:
Rewind never stores its URL, password, connection pool, or connection options.

```python
import redis.asyncio as redis
from rewind.adapters.redis import AsyncRecordingRedis

cache = AsyncRecordingRedis(redis.Redis(decode_responses=False), dependency="cache")

async def lookup():
    async with cache.pipeline(transaction=True) as pipe:
        pipe.set("fixture", "value", ex=60).get("fixture")
        return await pipe.execute()
```

Execute `lookup` through `Rewind.run`, an instrumented request, or a captured
function. Synchronous code uses `RecordingRedis` and ordinary non-awaited
commands and pipeline execution. During replay you can construct the wrapper
with no client at all: `AsyncRecordingRedis(dependency="cache")`. Supported
operations only consume the snapshot. They never create a Redis client or call
the real client during replay. Use the same explicit dependency name and call
sequence in both executions.

Commands record method, positional arguments and keyword arguments. Pipelines
record the queued sequence, transaction flag, shard hint, `raise_on_error`, and
one result for the entire execution. Changes in keys, values, command order,
options, dependency names, or pipeline mode diverge. Results preserve strings,
bytes, bytes-key dictionaries, tuples, lists, sets, integers, floats, booleans,
and `None`. Transactional and nontransactional pipelines support chaining,
reset, context managers, reuse, and `execute(raise_on_error=False)` error entries.
A pipeline queued outside the current capture is ineligible.

Supported methods cover ordinary string/key, hash, list, set, sorted-set,
nonblocking scan-page, ping, and echo commands. The explicit allowlist is
`_METHODS` in `rewind.adapters.redis`. `execute_command` supports those same
command names, including bytes command names. Iterator helpers such as
`scan_iter`, custom response callbacks returning arbitrary objects, cluster and
Sentinel APIs, Pub/Sub, blocking commands, Lua/scripts, WATCH transactions,
administration, and authentication commands are outside this adapter's scope.
Unsupported calls pass through unchanged during capture and mark the artifact
incomplete; replay rejects them without calling Redis. Wrap application calls
through this object: accessing the original client directly bypasses capture.
This is not a general drop-in replacement for every redis-py attribute or helper.

Recognized exceptions are redis-py's `ConnectionError`, `TimeoutError`,
`ResponseError`, `DataError`, `BusyLoadingError`, `AuthenticationError`,
`AuthorizationError`, `ReadOnlyError`, and `NoScriptError`, with string/integer
arguments. Their exact class and arguments survive replay when the capture
policy permits them. Original application exceptions always propagate unchanged.
Unknown exception classes mark the capture incomplete and their messages are
not saved. Applications using exception attributes beyond type and `args` are
outside the contract.

The default policy excludes command values, results, and exception arguments,
so these artifacts are not replay eligible. Use `CapturePolicy.synthetic()`
only for fixtures known to contain no secrets: Redis positional keys/values and
exception text can contain arbitrary private data. Recognized sensitive hash
fields are redacted, including bytes field names, and redaction makes the
artifact incomplete. Queue snapshots and result structures are bounded by the
configured capture limits; exceeding them does not interrupt live Redis calls.

Run fake-backed contract cases with `pytest tests/test_redis.py`. To additionally
exercise a real server, install `redis-server` and run:

```sh
REWIND_REDIS_CONFORMANCE=1 pytest tests/test_redis.py
```

Each conformance case creates a temporary Redis instance with TCP disabled,
no persistence, and a private Unix socket. Both client APIs and both response
decoding modes run commands and both pipeline types; replay then runs with the
real client removed. The server is terminated at fixture teardown.

References: [redis-py asyncio and pipeline behavior](https://redis.readthedocs.io/en/v5.0.0/examples/asyncio_examples.html),
[Redis pipeline and transaction guide](https://redis.io/docs/latest/develop/clients/redis-py/transpipe/).
