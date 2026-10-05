# Rewind

**Capture a Python failure. Replay the observations that caused it.**

Rewind records selected dependency calls while your application runs, then uses
those recorded observations to reproduce the outcome locally. Replay checks the
call order, arguments, and result; a missing or different observation produces
a divergence report instead of falling back to a live dependency.

Install the **`jaysoft-rewind`** distribution, import **`rewind`**, and use the
**`rewind`** command. The project is maintained by Jay Prakash Sonkar and licensed
under the MIT license.

[Documentation](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/index.md)
· [Getting started](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/getting-started.md)
· [Source](https://github.com/iamjpsonkar/JaySoft-Rewind)
· [Changelog](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/CHANGELOG.md)

## Install 0.2.0a2

This guide covers **Rewind `0.2.0a2`**, an alpha release for Python 3.11 and 3.12.

```sh
python -m pip install "jaysoft-rewind==0.2.0a2"
rewind --version
```

The core package has no mandatory third-party runtime dependencies. Install an
optional integration with the matching extra: `httpx`, `fastapi`, `flask`,
`sqlalchemy`, `redis`, `postgres`, `mysql`, `kafka`, `celery`, `s3`, `encryption`, or `all`. For example:

```sh
python -m pip install "jaysoft-rewind[fastapi]==0.2.0a2"
# Or, for Redis:
python -m pip install "jaysoft-rewind[redis]==0.2.0a2"
```

See the [installation guide](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/installation.md)
for environment setup and version selection.

## What you can capture in 0.2.0a2

- **Functions and requests:** synchronous or asynchronous callables, FastAPI/ASGI
  HTTP requests, and Flask/WSGI requests.
- **Dependency observations:** HTTPX requests, synchronous SQLite DB-API and
  SQLAlchemy operations with SQLite, PostgreSQL and MySQL; supported Redis
  commands and pipelines; explicit Kafka/Celery, filesystem and S3 observations.
- **Sources of variation:** explicit time, date, UUID, random, environment, and
  wait observations through Rewind's source APIs.
- **Optional diagnostics:** bounded function-entry, return, exception, and named
  span events, with dropped-event counts.

The developer tools inspect recordings, replay them in a fresh process, compare
changed source against an explicit expected outcome, generate pytest cases, and
export or import validated portable archives. Storage, capture limits, retention
conditions, and optional background persistence control the amount of data kept.

Adapters are explicit: installing Rewind alone does not intercept your clients
or instrument your application. The
[support matrix](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/support-matrix.md)
provides the tested contracts and exclusions for each integration.

## Decorator workflow

Import one decorator and supply an ordinary Python condition block:

```python
from rewind import capture

def save_when(call):
    return call.error is not None

@capture(condition=save_when)
def total(order):
    return order["quantity"] * order["unit_price"]
```

Save the example in `payment_logic.py`, then call `total` normally. A failing call
is saved under `.rewind/snapshots`; its original exception still reaches the caller.
No condition (`@capture`) means retain every admitted call. The same decorator on
a class wraps its declared public methods and captures supported instance state.

```sh
python -c 'from payment_logic import total; total({"quantity": 2})'
rewind list --store .rewind/snapshots
artifact=.rewind/snapshots/PASTE_SNAPSHOT_ID.rewind.json
rewind explore "$artifact" --output report.html
rewind replay "$artifact" --app payment_logic:total
```

Choose the ID shown by `list`. The first command raises the expected `KeyError`;
replay executes the function again and confirms the same error. No application
factory or middleware is needed. For a method use `--app payment_logic:Checkout.total`.
See the [decorator guide](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/decorator-guide.md)
for conditions based on results, arguments and duration, plus supported state and
dependency limits. External calls are not automatically intercepted.

## Optional server middleware

Configure Rewind in the server process, wrap its supported dependencies, and send
an ordinary request. Keep every admitted request with `Retention(always=True)`,
or use a status/exception/duration condition. Explore a retained artifact with
`rewind explore snapshot.rewind.json --output report.html`, then replay the same
handler with `rewind replay snapshot.rewind.json --app app:replay_target`.

The server tutorial demonstrates a real HTTP 500 caused by an incomplete provider
response, followed by reproduction after stopping the server. The HTML explorer
works locally and contains the recorded request, dependency calls, handler result
or exception, and optional function timeline. It does not upload artifacts.

## Optional: try a small callable failure

Save this as `rewind_demo.py` and run
`python rewind_demo.py`. It uses only synthetic fixture data and the core package.

```python
from tempfile import TemporaryDirectory

from rewind import CapturePolicy, LocalStore, Retention, Rewind

with TemporaryDirectory() as directory:
    store = LocalStore(directory)
    recorder = Rewind(
        application="checkout-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
        retain=Retention(always=True),
    )
    live_calls = 0

    def payment_reply():
        global live_calls
        live_calls += 1
        return {"status": "accepted"}  # The fixture is missing receipt_id.

    def checkout():
        reply = recorder.value("payment.reply", payment_reply)
        return reply["receipt_id"]

    try:
        recorder.run_sync(checkout)
    except KeyError:
        pass

    snapshot = store.load(store.ids()[0])
    report = recorder.replay_sync(snapshot, checkout)
    assert report.reproduced, report.to_dict()
    assert live_calls == 1
    print(report.status)
    print(f"Live provider calls: {live_calls}")
```

Expected output:

```text
reproduced
Live provider calls: 1
```

The original execution raises `KeyError`. Replay produces the same failure from
the recorded reply without calling `payment_reply` again. Reproducing a failure
means it was faithfully observed; it does not mean the application has been
fixed. Use an explicit expected outcome when comparing a proposed fix.

The temporary recording is removed when this example ends. The
[getting-started guide](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/getting-started.md)
shows how to keep artifacts and connect the workflow to your application.

## Capture policy and replay boundaries

The lower-level `Rewind(...)` defaults exclude application values and bodies.
The explicit `@capture` decorator enables supported argument, result, instance
state and exception-argument capture, with named-field redaction. The synthetic policy in
the example opts into fixture values and exception arguments; use it only with
data you know contains no secrets. Named-field redaction, capture limits, and
unsupported operations can make an artifact incomplete. Incomplete artifacts
remain inspectable and are not eligible for strict replay.

Strict reproduction requires a compatible application fingerprint, Python and
adapter environment, policy, and entry point. Changed-source comparison is an
explicit separate mode. Replay reproduces supported observations; it does not
reconstruct a whole operating system, arbitrary native code, or a distributed
service environment. The default fresh-process Python audit guard is a
Python-level boundary. Network-disabled Docker verification provides a separate
OS-level network boundary for the included examples.

## Next steps

- [HTTPX and FastAPI](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/http-and-fastapi.md)
- [SQLite and SQLAlchemy](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/database.md)
- [Redis commands and pipelines](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/redis.md)
- [Explicit deterministic sources](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/sources.md)
- [Command-line reference](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/cli.md)
- [Troubleshooting](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/troubleshooting.md)
- [Report an issue](https://github.com/iamjpsonkar/JaySoft-Rewind/issues)

## Validation and compatibility

The [local staging suite](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/deployment-validation.md)
measures fixed-arrival load, independent memory use, queue budgets, disable/drain,
and rollback. The bundled profile uses synthetic data and stated example budgets.
See the [compatibility policy](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/compatibility-policy.md)
for alpha API, schema and release guarantees.

New to Rewind? Read the [decorator walkthrough](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/decorator-guide.md)
for a complete first recording, offline replay, and existing-backend example.
