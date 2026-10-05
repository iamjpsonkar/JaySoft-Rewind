# Time, randomness and environment values

[Documentation home](index.md) · [Capture conditions](portable-tooling.md)

Clock, date, UUID and basic random sources are available in `0.1.0a2`. Environment reads, synchronous waits, `getrandbits` and `randrange` require development `0.1.0a3`.


Use `rewind.sources` at application observation sites inside a captured execution:

```python
from datetime import UTC

# Inside an async entry point using the Rewind instance named rewind:
created_at = rewind.sources.datetime_now(UTC)
request_id = rewind.sources.uuid4()
retry_delay = rewind.sources.uniform(0.1, 0.5)
await rewind.sources.sleep(retry_delay)
```

These operations record actual outcomes in interaction order. Replay returns the recorded values and checks method arguments without calling live clock, UUID, or random factories. Recorded sleep yields once without waiting the original delay.

Run the source-observation example to reproduce a synthetic booking failure whose exception arguments include the captured clock, date, UUID, and random values:

```sh
artifact="$(python -m examples.sources_failure)"
rewind inspect "$artifact"
rewind replay "$artifact" --app examples.sources_failure:replay_target
```

The example also records a short async wait. A `reproduced` result checks the exact observation values through the recorded failure, as well as consuming every expected source interaction.

| Family | Explicit methods |
|---|---|
| Clocks | `time`, `time_ns`, `monotonic`, `monotonic_ns`, `perf_counter`, `perf_counter_ns` |
| Dates | `datetime_now(tz=None)`, `date_today()` |
| Identifiers | `uuid4()` |
| Random values | `random()`, `randint(a, b)`, `uniform(a, b)`, `getrandbits(k)`, `randrange(start, stop=None, step=1)` |
| Collections | `choice(population)`, `sample(population, k)`, `shuffle(list)` |
| Waits | `await sleep(delay, result=None)`, `sleep_sync(delay)` |
| Environment | `getenv(key, default=None)`, `environ(key)` |

Date/datetime/UUID values use typed codecs. Datetimes support naive values and standard-library fixed-offset timezones, including UTC; `ZoneInfo` and custom timezone classes are outside the current contract. Collection elements must use supported codec types. Shuffle mutates the supplied list and returns `None`; object identity of elements is not reproduced.

Direct standard-library calls, imported aliases, third-party/native RNGs, and cryptographic entropy sources are not automatically intercepted. Rewind reproduces observations, not RNG internal state, elapsed wall time, or task scheduling. Capture policy applies to these values too: the default policy excludes them; controlled synthetic fixtures can opt in with `CapturePolicy.synthetic()`.

## Environment values

Use `rewind.sources.getenv("REGION", "local")` for an optional value or `rewind.sources.environ("REGION")` for a required value. A missing required key can be replayed as `KeyError`. Known secret keys are excluded and make the capture ineligible. See [redaction and environment behavior](portable-tooling.md#environment-and-synchronous-waits).
