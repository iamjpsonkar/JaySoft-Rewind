# Rewind

**New to Rewind? [How to use Rewind in your server](docs/server-guide.md)** — plug in, send a request, explore its snapshot, and replay the same handler.

**Capture a Python failure. Replay it locally. Turn it into a test.**

[![Checks](https://github.com/iamjpsonkar/JaySoft-Rewind/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/iamjpsonkar/JaySoft-Rewind/actions/workflows/checks.yml)
[![PyPI](https://img.shields.io/pypi/v/jaysoft-rewind)](https://pypi.org/project/jaysoft-rewind/)
[![Python 3.11 and 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](docs/support-matrix.md)
[![MIT license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Quick start](#quick-start) · [Choose an example](#choose-an-example) · [Documentation](docs/index.md) · [Troubleshooting](docs/troubleshooting.md) · [Changelog](CHANGELOG.md)

Rewind records the dependency observations your application receives, then supplies those observations during local replay. See whether the original failure reproduced, identify where execution diverged, and save a repeatable test.

```text
Capture a failing execution → Inspect the recording → Replay locally → Generate a test
```

## How to use

1. **Plug Rewind into your server.** Wrap the FastAPI/ASGI app and configure the
   supported dependency adapters used by its handler.
2. **Choose retention.** Use `Retention(always=True)` to record every admitted
   request, or `Condition.parse("exception or status >= 500")` to retain failures.
3. **Hit your normal endpoint.** The client sends an ordinary HTTP request.
4. **Explore the artifact.** Run `rewind explore snapshot.rewind.json --output report.html`.
5. **Replay the handler.** Run `rewind replay snapshot.rewind.json --app app:replay_target`.

The [server walkthrough](docs/server-guide.md) includes the complete runnable
application, curl commands, conditional capture, browser exploration and offline
reproduction. No condition in its setup means record every admitted request.
Replay checks the recorded request, supported dependency observations and outcome;
unsupported or incomplete capture is reported explicitly.

## Choose your version

| I want to… | Start here |
| --- | --- |
| Install this alpha | `python -m pip install 'jaysoft-rewind[httpx]==0.2.0a1'` |
| Run the repository examples | Use the source checkout below |
| Add Rewind to an application | [Installation and optional integrations](docs/installation.md) |
| Browse the package overview | [PyPI guide](docs/pypi.md) · [Published package](https://pypi.org/project/jaysoft-rewind/) |

**Release candidate: `0.2.0a1`.** [Package releases](https://pypi.org/project/jaysoft-rewind/#history) are available on PyPI. The distribution is `jaysoft-rewind`; the Python import and command are both `rewind`. Use Python 3.11 or 3.12. This alpha is intended for synthetic fixtures and controlled development environments.

## Quick start

Run a small instrumented server, send a normal request, then replay its handler.
Use Python 3.11 or 3.12 and a macOS/Linux shell.

**1. Install and start the demo server.**

```sh
git clone https://github.com/iamjpsonkar/JaySoft-Rewind.git
cd JaySoft-Rewind
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[fastapi]'
python -m examples.server_demo --store .rewind/server-demo --port 8000
```

**2. Send a request from a second terminal.**

```sh
curl -i http://127.0.0.1:8000/quote \
  -H 'Content-Type: application/json' \
  -d '{"sku":"demo-widget","quantity":2}'
```

The handler returns HTTP 500 because its provider response lacks `unit_price`.
Rewind retains the request, provider observation, exception and optional handler
timeline. No `--when` in this demo means every admitted request is retained.

**3. Explore the snapshot.** In the project directory, activate the same environment:

```sh
. .venv/bin/activate
rewind list --store .rewind/server-demo
artifact=.rewind/server-demo/PASTE_SNAPSHOT_ID.rewind.json
rewind explore "$artifact" --output report.html
```

Replace `PASTE_SNAPSHOT_ID` with the listed ID. Open `report.html` in your browser.

**4. Stop the server, then replay the same handler.**

```sh
rewind replay "$artifact" --app examples.server_demo:replay_target
```

Expected report: `"status": "reproduced"`, `"consumed": 1`, `"total": 1`.
The handler receives the recorded request and provider response, then raises the
same error. The replay factory forbids live provider calls.

**5. Choose a condition when needed.**

```sh
python -m examples.server_demo --when 'exception or status >= 500'
```

[Full beginner server walkthrough →](docs/server-guide.md) ·
[Generate a test or compare a fix →](docs/comparison.md)

## Choose an example

Run these from the installed source checkout. Click a use case to expand its commands.

<details>
<summary><strong>FastAPI: reproduce a request returning HTTP 502</strong></summary>

```sh
artifact="$(python -m examples.fastapi_failure)"
rewind replay "$artifact" --app examples.fastapi_failure:replay_target
```

The example uses an in-process FastAPI app and a synthetic upstream 503 response. [HTTPX and FastAPI guide →](docs/http-and-fastapi.md)

</details>

<details>
<summary><strong>Database + Redis + HTTP: replay one execution across dependencies</strong></summary>

Requires `0.1.0a3`. The capture example uses local SQLite and synthetic Redis/HTTP fixtures; no Redis server is needed.

```sh
artifact="$(python -m examples.combined_failure)"
rewind inspect "$artifact" --timeline
rewind replay "$artifact" --app examples.combined_failure:replay_target
```

[Database guide →](docs/database.md) · [Redis guide →](docs/redis.md)

</details>

<details>
<summary><strong>Time and randomness: reproduce the same observed values</strong></summary>

```sh
artifact="$(python -m examples.sources_failure)"
rewind replay "$artifact" --app examples.sources_failure:replay_target
```

Use explicit `rewind.sources` methods for clock, UUID and random observations. Replay returns recorded values and skips the original sleep delay. [Sources guide →](docs/sources.md)

</details>

<details>
<summary><strong>Background capture: queue, drain and replay</strong></summary>

```sh
python -m examples.background_capture --store .rewind/background-demo
```

The example prints capture statistics and its replay report. [Background persistence →](docs/background-persistence.md) · [Operations →](docs/operations.md)

</details>

## Find your integration

The application explicitly configures each adapter. Installing an extra or wrapping a framework does not automatically intercept every dependency.

| Your application uses… | Guide | Available in |
| --- | --- | --- |
| Async HTTPX and FastAPI/ASGI | [HTTP and FastAPI](docs/http-and-fastapi.md) | Since `0.1.0a2` |
| Clocks, UUIDs and basic random values | [Deterministic sources](docs/sources.md) | Since `0.1.0a2` |
| Bounded background recording | [Persistence](docs/background-persistence.md) | Since `0.1.0a2` |
| Sync callables, HTTPX or Flask/WSGI | [Synchronous applications](docs/synchronous.md) | Since `0.1.0a3` |
| SQLAlchemy 2.x with SQLite | [Database capture and replay](docs/database.md) | Since `0.1.0a3` |
| redis-py sync/async commands and pipelines | [Redis capture and replay](docs/redis.md) | Since `0.1.0a3` |
| Function timelines and named spans | [Tracing](docs/tracing.md) | Since `0.1.0a3` |
| Environment reads, conditions or custom redaction | [Portable tooling and policy](docs/portable-tooling.md) | Since `0.1.0a3` |
| Changed code and regression assertions | [Comparison](docs/comparison.md) | Since `0.1.0a3` |

## Keep the CLI close

| Task | Command |
| --- | --- |
| See available commands | `rewind --help` |
| Find captured HTTP demo recordings | `rewind list --store .rewind/demo` |
| Explore a recording in your browser | `rewind explore "$artifact" --output report.html` |
| Inspect a recording | `rewind inspect "$artifact"` |
| Show recorded dependency timing | `rewind inspect "$artifact" --timeline` |
| Share a recording archive | `rewind export "$artifact" -o failure.rewind` |
| Import an archive | `rewind import failure.rewind --store .rewind/imported` |
| Check installed integration versions | `rewind doctor` |

Timeline inspection, export/import and doctor require `0.1.0a3`. Archives contain recording data, not your application code. [Full command reference and exit codes →](docs/cli.md)

## Questions before you start

<details>
<summary><strong>Why is my recording marked ineligible?</strong></summary>

The default policy omits values, bodies and exception arguments. If replay needs omitted data, the recording is ineligible. For known synthetic fixtures, examples opt in with `CapturePolicy.synthetic()`. Inspect `ineligible_reasons`; unsupported operations or exceeded limits can also prevent replay. [Troubleshooting →](docs/troubleshooting.md)

</details>

<details>
<summary><strong>Can I replay after fixing my code?</strong></summary>

Strict replay checks the declared source fingerprint. Use `rewind compare` in `0.1.0a3` to explicitly allow a changed source digest. Dependency order, inputs and other compatibility checks still apply. Supply the desired outcome yourself. [Comparison guide →](docs/comparison.md)

</details>

<details>
<summary><strong>Does replay contact the original services?</strong></summary>

Supported adapters return recorded observations without live fallback. The default Python audit guard blocks selected external operations, but is not an OS sandbox. To verify the repository examples with an independent network boundary, run `./scripts/verify_offline.sh`. Docker needs network access during image build; replay runs with `--network none`. [Security model →](SECURITY.md)

</details>

<details>
<summary><strong>What are the current limits?</strong></summary>

Capture is bounded: defaults include 64 KiB per body, 1 MiB per artifact and 1,000 interactions. Supported operations within one capture are sequential. Rewind does not reconstruct arbitrary process state, scheduling, unwrapped clients or every database driver. Key-based redaction is not a universal secret detector. [Supported boundaries →](docs/support-matrix.md) · [Operational limits →](docs/operations.md)

</details>

## Documentation and contributing

[Documentation home](docs/index.md) brings together walkthroughs, adapter guides, the [snapshot specification](docs/snapshot-format.md), [release guide](docs/releases.md) and [implementation ledger](docs/implementation-roadmap.md).

Validation for `0.1.0a3` includes 488 passing default-suite tests, a separate 84-test Redis run that includes the four normally skipped real-server cases, and seven offline Docker examples. The suites overlap. [CI](https://github.com/iamjpsonkar/JaySoft-Rewind/actions/workflows/checks.yml) checks Python 3.11/3.12, lint, types and distributions. [Measured benchmarks](docs/benchmarks/README.md) describe synthetic workloads; production readiness remains a separate evidence gate.

Found a problem? Check [troubleshooting](docs/troubleshooting.md), then [open an issue](https://github.com/iamjpsonkar/JaySoft-Rewind/issues) with your version and a synthetic reproduction. See [contributing](CONTRIBUTING.md) for development setup and [SECURITY.md](SECURITY.md) for sensitive reports.

Maintained by [Jay Prakash Sonkar](https://github.com/iamjpsonkar) · [iamjpsonkar@gmail.com](mailto:iamjpsonkar@gmail.com) · [MIT license](LICENSE).

[Back to top ↑](#rewind)

### Expanded integrations and deployment validation

The `0.2.0a1` candidate adds explicit [PostgreSQL/MySQL](docs/external-databases.md),
[Kafka/Celery](docs/messaging.md), and [filesystem/S3](docs/filesystem-and-s3.md)
boundaries. Each guide states the supported calls and its conformance environment.

Run the [local synthetic staging suite](docs/deployment-validation.md) to measure
fixed-arrival load, memory, queue budgets and disable/drain/rollback behavior.
[Measured reports](docs/validation/README.md) retain the configuration and source
revision that produced each result.

[Protected-branch workflow](docs/repository-governance.md) ·
[API and artifact compatibility](docs/compatibility-policy.md)
