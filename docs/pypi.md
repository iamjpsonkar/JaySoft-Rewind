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

## Choose your version

**The published PyPI release is `0.1.0a2`. Development on `main` is
`0.1.0a3`, which has not been published yet.** Rewind is alpha software; the
supported runtime versions are Python 3.11 and 3.12.

Install the published release:

```sh
python -m pip install "jaysoft-rewind==0.1.0a2"
rewind --version
```

Version `0.1.0a2` provides async callable capture, async HTTPX/ASGI integration,
explicit deterministic sources, bounded background persistence, and local
inspection/replay tools. See its
[release documentation](https://github.com/iamjpsonkar/JaySoft-Rewind/tree/v0.1.0a2/docs)
for the APIs available in that release.

The features and example below describe **development `0.1.0a3`**. To try them
before its release, install the current source tree; this requires Git:

```sh
python -m pip install "jaysoft-rewind @ git+https://github.com/iamjpsonkar/JaySoft-Rewind.git@main"
```

Optional integrations are separate extras. On the development source tree, choose
`httpx`, `fastapi`, `flask`, `sqlalchemy`, `redis`, or `all`; for example:

```sh
python -m pip install "jaysoft-rewind[fastapi] @ git+https://github.com/iamjpsonkar/JaySoft-Rewind.git@main"
```

See the [installation guide](https://github.com/iamjpsonkar/JaySoft-Rewind/blob/main/docs/installation.md)
for environment setup and version selection.

## What you can capture in 0.1.0a3

- **Functions and requests:** synchronous or asynchronous callables, FastAPI/ASGI
  HTTP requests, and Flask/WSGI requests.
- **Dependency observations:** HTTPX requests, synchronous SQLite DB-API and
  SQLAlchemy SQLite operations, and supported Redis commands and pipelines.
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

## Try a failure and its replay

**Requires development `0.1.0a3`.** Save this as `rewind_demo.py` and run
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

Capture defaults exclude application values and bodies. The synthetic policy in
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
