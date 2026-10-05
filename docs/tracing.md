# Bounded function diagnostics

[Documentation home](index.md) · [Quick start](getting-started.md)

Function tracing is explicit and off by default. Enable it on a capture instance
and decorate selected application functions:

```python
from rewind import Rewind
from rewind.tracing import TraceConfig, span, trace

rewind = Rewind(
    application="billing",
    code_paths=[__file__],
    trace_config=TraceConfig(enabled=True, max_events=256, max_bytes=65536),
)

@trace(name="billing.calculate")
def calculate():
    with span("validate"):
        return 42
```

`@trace` also works without arguments, deriving a static name from the function.
Declared coroutine functions are awaited normally. `span` supports both `with`
and `async with`. Names must be identifiers of at most 128 characters using
letters, digits, underscores, dots, colons, and hyphens, beginning with a letter
or underscore. Use static code labels rather than user input or identifiers
containing business data.

Events contain a sequence number, span and parent IDs, function/manual-span
kind, enter/return/exception phase, name, monotonic offset and elapsed nanoseconds,
and the exception class name when applicable. They contain no call arguments,
return values, locals, source snippets, stack objects, or exception messages.
Timing describes the capture execution; replay does not reproduce diagnostic
timings or require diagnostic events to match.

Each capture has its own ring, capped by event count and bytes. The oldest
optional events are evicted when full. `diagnostics.dropped` reports events lost
to these limits and the recursion-depth limit, so a retained timeline can start
mid-span. Loss of optional diagnostics does not remove required dependency
observations or change replay eligibility. At finalization, optional events are
trimmed again to fit space left after required snapshot data. If even an empty
summary will not fit, the diagnostics field is omitted. Optional collection
failures preserve the application result or original exception.

The configurable maximum depth defaults to 32. Recursive calls remain normal
application calls; events beyond the diagnostic depth are dropped. Async child
tasks inherit their parent span, and explicitly copied thread contexts can add
events safely. This support applies to diagnostics only: dependency replay
retains its sequential task/thread ownership rules. Threads without copied
context do not collect events. An inherited buffer in a forked process is
inactive and never takes a possibly inherited locked mutex. Construct captures
after forking.

No global profiling hooks, monkeypatches, or automatic code scanning are
installed. Generator and async-generator functions are rejected at decoration;
use spans around their consumed work instead. A synchronous function returning
an awaitable is measured until it returns that awaitable, not until it is
subsequently awaited. Span context-manager instances are single-use. Custom
decorators and ordinary bound methods work when applied to the underlying
function; apply `@trace` inside `@classmethod` or `@staticmethod`.

## Measured overhead

A local microbenchmark on Python 3.12.13 / macOS 26.6 ARM64 used a function that
returns `1`, 20,000 calls per repetition, and five repetitions. Median time per
call was:

| Case | Nanoseconds per call |
| --- | ---: |
| Plain function | 24.7 |
| Decorated, outside capture | 129.5 |
| Decorated, diagnostics disabled | 227.6 |
| Decorated, diagnostics enabled | 10,759.1 |

The enabled run retained 256 events and reported 199,744 dropped events after
100,000 calls. This measures decorator and two-event ring costs on a trivial
function, including serialization sizing; it is not a request-level overhead
claim. Select coarse functions deliberately and measure your workload. There
are no artificial light/standard/deep modes: explicit enablement, selected
functions, and finite count/byte/depth limits determine the cost.

Reproduce the measurement from a checkout:

```python
import statistics
import timeit
from types import SimpleNamespace
from rewind import context
from rewind.tracing import TraceBuffer, TraceConfig, trace

def plain():
    return 1

traced = trace(plain, name="benchmark.call")
for label, function, config in [
    ("plain", plain, None),
    ("outside", traced, None),
    ("disabled", traced, TraceConfig()),
    ("enabled", traced, TraceConfig(enabled=True)),
]:
    buffer = TraceBuffer(config) if config else None
    active = SimpleNamespace(diagnostics=buffer, sealed=False) if buffer else None
    token = context.current.set(active)
    try:
        samples = timeit.repeat(function, number=20000, repeat=5)
        print(label, statistics.median(samples) / 20000 * 1e9)
    finally:
        if buffer:
            buffer.clear()
        context.current.reset(token)
```
