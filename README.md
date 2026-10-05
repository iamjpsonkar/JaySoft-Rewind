# Rewind

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

## Choose your version

| I want to… | Start here |
| --- | --- |
| Install this alpha | `python -m pip install 'jaysoft-rewind[httpx]==0.1.0a3'` |
| Run the repository examples | Use the source checkout below |
| Add Rewind to an application | [Installation and optional integrations](docs/installation.md) |
| Browse the package overview | [PyPI guide](docs/pypi.md) · [Published package](https://pypi.org/project/jaysoft-rewind/) |

**Release: `0.1.0a3`.** [Package releases](https://pypi.org/project/jaysoft-rewind/#history) are available on PyPI. The distribution is `jaysoft-rewind`; the Python import and command are both `rewind`. Use Python 3.11 or 3.12. This alpha is intended for synthetic fixtures and controlled development environments.

## Quick start

Try a complete failure without API keys, databases or a running web server. These commands use a macOS/Linux shell; [PowerShell commands](docs/installation.md#windows-powershell) are also available.

**1. Install the examples from source.**

```sh
git clone https://github.com/iamjpsonkar/JaySoft-Rewind.git
cd JaySoft-Rewind
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

**2. Capture and replay a synthetic HTTP failure.**

```sh
artifact="$(python -m examples.http_failure)"
rewind inspect "$artifact"
rewind replay "$artifact" --app examples.http_failure:replay_target
```

The provider fixture omits `payment_id`, causing a `KeyError`. The replay report should contain:

```json
{"status": "reproduced", "consumed": 1, "total": 1}
```

This is an excerpt: `reproduced` means the same recorded failure occurred and every expected interaction matched. The process exits with code `0`.

**3. Save a reproduction test.**

```sh
rewind test "$artifact" --app examples.http_failure:replay_target --output test_reproduction.py
python -m pytest test_reproduction.py
```

The generated test asserts the recorded outcome, including the failure. When you fix the application, use [explicit comparison and desired outcomes](docs/comparison.md) to test the intended behavior. Test generation refuses to overwrite existing tests or fixtures.

[Follow the full walkthrough →](docs/getting-started.md)

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

Supported adapters return recorded observations without live fallback. The default Python audit guard blocks selected external operations, but is not an OS sandbox. To verify the seven repository examples with an independent network boundary, run `./scripts/verify_offline.sh`. Docker needs network access during image build; replay runs with `--network none`. [Security model →](SECURITY.md)

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

### Next alpha integration work

The `0.2.0a1` development candidate adds explicit filesystem and S3 boundaries;
see [supported methods and examples](docs/filesystem-and-s3.md). Additional
relational and messaging conformance is being integrated. Published `0.1.0a3`
remains unchanged until the next candidate passes release validation.

[Protected-branch workflow](docs/repository-governance.md) documents the required
checks and pull-request merge rules.
