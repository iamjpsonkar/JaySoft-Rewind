# Rewind

Capture a supported Python execution and replay its recorded dependency observations in a fresh local process. Rewind reports whether the original outcome reproduced or where execution diverged.

**Published alpha: `0.1.0a2`. Development: `0.1.0a3`.** Intended for synthetic fixtures and controlled development environments. The development version adds SQLite/SQLAlchemy, Redis, synchronous HTTPX and Flask/WSGI support. Production deployment and arbitrary Python process replay remain outside the supported scope.

Maintained by [Jay Prakash Sonkar](https://github.com/iamjpsonkar) · [iamjpsonkar@gmail.com](mailto:iamjpsonkar@gmail.com) · [MIT license](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/LICENSE).

## Install

Use Python 3.11 or 3.12. The distribution is [`jaysoft-rewind`](https://pypi.org/project/jaysoft-rewind/0.1.0a2/); the import and CLI remain `rewind`.

```sh
python -m pip install 'jaysoft-rewind==0.1.0a2'
rewind --version
```

For HTTPX or FastAPI applications, install `'jaysoft-rewind[httpx]==0.1.0a2'` or `'jaysoft-rewind[fastapi]==0.1.0a2'`.

For development and the repository examples, install from source:

```sh
git clone https://github.com/iamjpsonkar/JaySoft-Rewind.git
cd JaySoft-Rewind
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
rewind --help
```

For application use from source without development tools, install `'.[httpx]'`, `'.[fastapi]'`, `'.[flask]'`, `'.[sqlalchemy]'`, `'.[redis]'`, or `'.[all]'`. The core has no required third-party runtime dependencies. Dependency ranges are installation constraints, not proof that every combination has been tested. See the [support matrix](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/support-matrix.md).

## Development capabilities

| Capability | Boundary and guide |
| --- | --- |
| HTTP | Explicit sync/async HTTPX 0.28.x transports; bounded buffered bodies |
| Frameworks | FastAPI/ASGI and [Flask/WSGI](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/synchronous.md); sync/async callable capture |
| Database | [SQLAlchemy 2.x + sqlite3](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/database.md), including cursor fetches, generated IDs and transaction outcomes |
| Redis | [Sync/async redis-py](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/redis.md) commands and pipelines; strict ordered observations |
| Determinism | Explicit clocks, dates, UUIDs, random operations, sync/async waits and environment reads |
| Diagnostics | [Bounded function/span timelines](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/tracing.md), required-interaction durations, optional-event drop counts |
| Portability | [Export/import, doctor and safe capture conditions](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/portable-tooling.md) |
| Fix verification | [Changed-code comparison](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/comparison.md) and explicit developer-approved regression outcomes |

These additions require the development source until `0.1.0a3` is published.

```sh
artifact="$(python -m examples.combined_failure)"
rewind inspect "$artifact" --timeline
rewind replay "$artifact" --app examples.combined_failure:replay_target
rewind export "$artifact" -o failure.rewind
rewind import failure.rewind --store .rewind/imported
rewind doctor
```

The combined example crosses HTTP, SQLite, Redis and explicit value sources. Replay uses recorded observations without accessing the original services. The application must configure each adapter; wrapping a framework does not intercept every dependency automatically.

## Reproduce an HTTP failure

Run from the repository root so the example factory can be imported. The example records a synthetic provider response missing `payment_id`; the application raises `KeyError`.

```sh
artifact="$(python -m examples.http_failure)"
rewind inspect "$artifact"
rewind replay "$artifact" --app examples.http_failure:replay_target
```

Expect `"status": "reproduced"` and exit code 0: the recorded failure reproduced, not a successful checkout. No real credentials or customer data are used.

The FastAPI example captures a buffered POST, an outbound provider error, and the resulting HTTP 502:

```sh
artifact="$(python -m examples.fastapi_failure)"
rewind replay "$artifact" --app examples.fastapi_failure:replay_target
```

Replay uses the explicitly selected factory in a fresh interpreter with a default 30-second timeout. The default `python-guard` profile blocks selected Python socket, subprocess, and native-loading audit events before application import. **It is not an operating-system sandbox.** For an independently enforced network boundary:

```sh
./scripts/verify_offline.sh
```

The script builds a local image, then verifies the HTTP, FastAPI, Flask, database, combined-dependency, deterministic-source, and background-persistence examples inside a read-only Docker container with `--network none`, temporary writable storage, and no added capabilities. Building needs network access for dependencies; replay runs without external networking. See [SECURITY.md](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/SECURITY.md).

## Integrate a callable or FastAPI app

Use an explicitly wrapped HTTPX async transport and declare the source paths relevant to replay:

```python
from pathlib import Path

import httpx

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind


def replay_target():
    rewind = Rewind(
        application="checkout-demo",
        code_paths=[Path(__file__)],
        store=LocalStore(".rewind/snapshots"),
        policy=CapturePolicy.synthetic(),  # Known synthetic fixture data only.
    )

    async def checkout(order_id):
        async with httpx.AsyncClient(transport=rewind.httpx_transport()) as client:
            response = await client.get(f"https://provider.example/orders/{order_id}")
            return response.json()["payment_id"]

    return ReplayTarget(rewind, checkout)


# During capture, in your application's existing event loop:
# target = replay_target()
# result = await target.rewind.run(target.entrypoint, "order-demo")
```

This snippet uses a live transport during capture. Supply your controlled service or use `httpx.MockTransport` as in the runnable examples. Replay consumes recorded interactions without calling the wrapped transport.

For FastAPI, wrap the app with `rewind.asgi(app)` and return `ReplayTarget(rewind, wrapped_app, kind="asgi")`. Middleware does not intercept other clients or databases. Supported requests consume their bodies, emit buffered responses, and perform dependency calls sequentially in the owning task. Configure local startup dependencies inside the factory; the runner does not drive ASGI lifespan.

`Rewind.value(name, factory)` records explicit observations. `Retention` supports exceptions, status thresholds, duration thresholds, and `always=True`; defaults retain exceptions and HTTP statuses of at least 500. The existing `store=` capture path writes retained artifacts synchronously.

## Background persistence and operational controls

Pass `writer=` to move filesystem writes off the request event loop. It is mutually exclusive with `store=`. Policy filtering and serialization still happen during capture.

```python
from rewind import BackgroundWriter, LocalStore, Rewind

writer = BackgroundWriter(
    LocalStore(".rewind/snapshots"), max_items=128, max_bytes=16 * 1024 * 1024,
)
rewind = Rewind(application="checkout", code_paths=[__file__], writer=writer)

# Wrap application calls with await rewind.run(...) or rewind.asgi(app).
# The default policy keeps diagnostic data; use synthetic policy only for fixtures.
rewind.disable()  # Stop new captures; already admitted requests still finish.
rewind.enable()
# At shutdown: stop incoming requests and await application tasks first.
# report = await rewind.aclose(timeout=5, drain=True)
```

`rewind.stats()` returns a safe copy of active reservations, pending bytes/items, accepted submissions, rejected recordings, completed writes, and failures. `aflush()` waits for writer idle; `aclose()` stops admission permanently and performs a bounded drain off the event loop. Neither method waits for application requests. A drained queue can still contain failed writes in its history; inspect `persistence_failed` as well. See [operations and rollback](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/operations.md).

The timeout bounds the writer wait after executor dispatch; it does not bound delays from an occupied executor or stalled event loop. Cancelling `aclose()` does not cancel the scheduled shutdown.

```sh
python -m examples.background_capture --store .rewind/background-demo
```

The example records, drains, and replays a synthetic failure, then prints capture statistics and the replay report.

`BackgroundWriter` provides a worker for already sealed snapshots. Configure both `max_items` and `max_bytes`; both budgets include the current filesystem write. `submit(snapshot)` returns immediately after bounded bookkeeping and rejects work when full. Acceptance means queued, not persisted.

`flush(timeout=5)` waits for idle. `close(timeout=5, drain=True)` stops submissions and bounds the drain wait; pending work is dropped at the deadline, while an active filesystem call can outlive it. `stats()` exposes completion, failure, rejection, and high-water counts without payload labels. See the [background persistence guide](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/background-persistence.md) for the complete contract and shutdown report. Disk writes move to a daemon thread; serialization still occurs before submission, and abrupt process exit can lose queued recordings.

## Record time, randomness, and UUIDs

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
| Random values | `random()`, `randint(a, b)`, `uniform(a, b)` |
| Collections | `choice(population)`, `sample(population, k)`, `shuffle(list)` |
| Waits | `await sleep(delay, result=None)` |

Date/datetime/UUID values use typed codecs. Datetimes support naive values and standard-library fixed-offset timezones, including UTC; `ZoneInfo` and custom timezone classes are outside the current contract. Collection elements must use supported codec types. Shuffle mutates the supplied list and returns `None`; object identity of elements is not reproduced.

Direct standard-library calls, imported aliases, third-party/native RNGs, and cryptographic entropy sources are not automatically intercepted. Rewind reproduces observations, not RNG internal state, elapsed wall time, or task scheduling. Capture policy applies to these values too: the default policy excludes them; controlled synthetic fixtures can opt in with `CapturePolicy.synthetic()`.

## Inspect, manage, and generate a test

```sh
artifact="$(python -m examples.http_failure)"
rewind list --store .rewind/demo
rewind inspect "$artifact"
rewind test "$artifact" --app examples.http_failure:replay_target --output test_reproduction.py
python -m pytest test_reproduction.py
# Delete one recording by its ID when no longer needed:
# rewind delete SNAPSHOT_ID --store .rewind/demo
```

Test generation copies a fixture beside the test and refuses to overwrite either file. It asserts the recorded outcome, including a failure. A developer must define desired assertions for a fix. Changed declared sources fail strict fingerprint preflight; no relaxed comparison mode is provided. Review fixtures before committing or sharing them.

| Replay result | Exit code | Meaning |
|---|---:|---|
| `reproduced` | 0 | All expected interactions and the observed outcome matched |
| `diverged` | 1 | Input, interaction sequence, or outcome differed |
| `ineligible`, `incompatible` | 2 | Incomplete capture or incompatible application/runtime |
| `replay_error` | 3 | Runner, timeout, or execution failure |

## Support and limits

| Supported in this alpha | Outside the current contract |
|---|---|
| Async callable and buffered ASGI HTTP entry points | Sync callables, WebSockets, SSE, streaming responses, lifespan replay |
| Explicit HTTPX async transport; sequential calls and retries | Child-task dependency calls, parallel calls within one execution, alternate clients |
| Independent simultaneous captures with isolated context | Race/scheduling reproduction, distributed execution, complete heap state |
| Bounded JSON/bytes and explicit value providers | Arbitrary Python objects, transparent global nondeterminism capture |
| Strict input and outcome comparison, no adapter fallback | Changed-source comparison or unmatched live calls |
| Versioned local JSON, atomic publication, TTL/quota cleanup on save | Encrypted storage, coordinated multi-process retention, crash-durable queues |
| Bounded background writer, enable/disable, metrics, finite shutdown | Cancellation of a running filesystem write, automatic application-task shutdown |

Default budgets: 64 KiB per body, 1 MiB per artifact, 1,000 interactions, and 64 MiB in active capture reservations. Default local storage: 256 MiB with 24-hour retention, enforced during saves. These are payload/accounting budgets, not process RSS guarantees or periodic cleanup. Publication can temporarily exceed quota; cleanup follows a successful write. Store locking is local to each `LocalStore` instance.

Default policy excludes values, bodies, and exception arguments. Known sensitive keys are redacted; policy transformations or missing required data make strict replay ineligible. `CapturePolicy.synthetic()` enables fixture capture, not general anonymization. Arbitrary strings, paths, headers, and binary data can contain secrets the key-based policy does not recognize.

Compatibility covers declared source files, Python major/minor, and installed supported adapter-library versions. It does not fingerprint the entire environment or discover all imported code. Include relevant source trees in `code_paths` and supply a compatible environment. Inspecting an artifact does not execute application code; replaying a factory does. `rewind compare` explicitly permits a changed source digest while retaining every other compatibility and interaction check; its result is labeled comparison.

## Package releases

[`0.1.0a2`](https://pypi.org/project/jaysoft-rewind/0.1.0a2/) was published on October 5, 2026, after TestPyPI verification. The [PyPI release plan](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/releases.md) covers alpha → beta → release-candidate → stable versions, exact version/tag checks, and OIDC trusted publishing. The [manual release workflow](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/.github/workflows/release.yml) defaults to validation only; publishing requires an explicit dispatch option.

## Development

Run the synthetic performance and queue-saturation toolkit with:

```sh
python -m scripts.benchmark_capture --output /tmp/rewind-benchmark.json
```

It compares baseline, disabled, discarded, synchronous, and background capture, then checks a gated failure storm. Reports include latency percentiles, CPU and drain time, allocation measurements, saved/rejected counts, and queue high-water marks. See [measurement methodology](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/performance.md); read persistence counts alongside throughput, since dropping recordings can make a saturated writer look faster.

The [published local sample](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/benchmarks/README.md) saved every measured artifact. Background capture reduced foreground disk waiting, while throughput including final drain remained comparable to synchronous persistence. Both failure storms respected their budgets and drained successfully. These synthetic observations do not establish production performance.

The published `0.1.0a2` passed 184 tests, Ruff, mypy and four offline Docker examples. Development CI additionally validates database/Redis/synchronous adapters, archives, timelines and explicit comparison, with real Redis conformance and resolved dependency-version artifacts. See the [completion ledger](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/implementation-roadmap.md) for current evidence and remaining gates.

The local alpha baseline at `8e143cc` passed 118 tests, lint, type checks, and distribution builds. [Its CI run](https://github.com/iamjpsonkar/JaySoft-Rewind/actions/runs/37316532798) also verified Python 3.11/3.12 and all three examples in Docker with external networking disabled. These checks validate the documented local scope; they do not establish production readiness.

Development proceeds in complete feature batches: implementation, meaningful tests, operational documentation, and runnable examples belong together on one branch and pull request. Parallel contributors use isolated worktrees and agree on interfaces before integration. README updates accompany each feature milestone; support claims follow observed checks.

```sh
ruff check src tests examples scripts
mypy src/rewind
python -m pytest -q
python -m build
```

See [CONTRIBUTING.md](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/CONTRIBUTING.md), [CHANGELOG.md](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/CHANGELOG.md), and the [project plan](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/REWIND_PROJECT_PLAN.md). Performance and production-readiness claims require further evidence.
