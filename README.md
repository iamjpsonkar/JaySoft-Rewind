# Rewind

**Start here: [Decorate a function or class](docs/decorator-guide.md)** — choose a condition, save matching calls, and replay them.

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

```python
from rewind import capture

def save_when(call):
    if call.error is not None:
        return True
    return call.result.get("success") is False

@capture(condition=save_when)
def process_order(order):
    return {"success": order["quantity"] > 0}
```

The decorated function keeps its normal return value and exceptions. After each
call, the condition decides whether to save its arguments and outcome. Apply the
same decorator to a class to capture its public methods and supported instance
state. Use `@capture` without a condition to keep every admitted call.

Replay accepts the decorated function or method directly; no replay factory or
server middleware is needed for this workflow. External dependency observations
still require supported adapters. See the [decorator guide](docs/decorator-guide.md)
for classes, conditions, privacy, and replay limits. Whole-request middleware is
also available in the [server guide](docs/server-guide.md).

## Choose your version

| I want to… | Start here |
| --- | --- |
| Install this alpha | `python -m pip install 'jaysoft-rewind==0.2.0a2'` |
| Run the repository examples | Use the source checkout below |
| Add Rewind to an application | [Installation and optional integrations](docs/installation.md) |
| Browse the package overview | [PyPI guide](docs/pypi.md) · [Published package](https://pypi.org/project/jaysoft-rewind/) |

**Release: `0.2.0a2`.** [Package releases](https://pypi.org/project/jaysoft-rewind/#history) are available on PyPI. The distribution is `jaysoft-rewind`; the Python import and command are both `rewind`. Use Python 3.11 or 3.12. This alpha is intended for synthetic fixtures and controlled development environments.

## Quick start

Use Python 3.11 or 3.12. Install the package:

```sh
python -m pip install 'jaysoft-rewind==0.2.0a2'
```

Save this as `payment_logic.py`:

```python
from rewind import capture

@capture(condition=lambda call: call.error is not None)
def total(order):
    return order["quantity"] * order["unit_price"]
```

Trigger a failure. The `KeyError` remains visible to the caller, and its snapshot
is saved to `.rewind/snapshots`:

```sh
python -c 'from payment_logic import total; total({"quantity": 2})'
rewind list --store .rewind/snapshots
```

Replace `PASTE_SNAPSHOT_ID` with the listed ID, then inspect and replay:

```sh
artifact=.rewind/snapshots/PASTE_SNAPSHOT_ID.rewind.json
rewind explore "$artifact" --output report.html
rewind replay "$artifact" --app payment_logic:total
```

Open `report.html` in your browser. Replay runs `total` again with the recorded
argument and confirms the same failure: `"status": "reproduced"`. This example
has no external dependencies, so its interaction count is zero.

[Full decorator walkthrough →](docs/decorator-guide.md) ·
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

Validation for `0.2.0a2`: 655 default-suite tests passed, with 28 opt-in service
cases skipped in that run. Separate real-service checks cover PostgreSQL, MySQL,
Redis, Kafka and Celery; nine Docker examples replay with networking disabled.
Ruff, mypy and Python 3.11/3.12 CI pass. The
[synthetic staging report](docs/validation/README.md) records workload budgets,
independent memory measurements and rollback evidence. These are explicit local
validation results, not production certification.

Found a problem? Check [troubleshooting](docs/troubleshooting.md), then [open an issue](https://github.com/iamjpsonkar/JaySoft-Rewind/issues) with your version and a synthetic reproduction. See [contributing](CONTRIBUTING.md) for development setup and [SECURITY.md](SECURITY.md) for sensitive reports.

Maintained by [Jay Prakash Sonkar](https://github.com/iamjpsonkar) · [iamjpsonkar@gmail.com](mailto:iamjpsonkar@gmail.com) · [MIT license](LICENSE).

[Back to top ↑](#rewind)

### Expanded integrations and deployment validation

The `0.2.0a1` alpha adds explicit [PostgreSQL/MySQL](docs/external-databases.md),
[Kafka/Celery](docs/messaging.md), and [filesystem/S3](docs/filesystem-and-s3.md)
boundaries. Each guide states the supported calls and its conformance environment.

Run the [local synthetic staging suite](docs/deployment-validation.md) to measure
fixed-arrival load, memory, queue budgets and disable/drain/rollback behavior.
[Measured reports](docs/validation/README.md) retain the configuration and source
revision that produced each result.

[Protected-branch workflow](docs/repository-governance.md) ·
[API and artifact compatibility](docs/compatibility-policy.md)
