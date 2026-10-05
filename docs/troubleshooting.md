# Troubleshooting

[Documentation home](index.md) · [Installation](installation.md) · [CLI reference](cli.md)

Start with `python -m rewind --version`. On development `0.1.0a3`, `python -m rewind doctor` also reports installed integration versions without contacting services or importing your application.

## Setup and commands

| Symptom | Check and next step |
| --- | --- |
| `rewind: command not found` | Activate the environment where you installed the package, or use `python -m rewind`. |
| `No module named rewind` | Install `jaysoft-rewind` into the same interpreter you are running; use `python -m pip`. |
| `No module named examples` | Examples live in the source checkout, not the wheel. Clone the repository, install `.[dev]`, and run from its root. |
| Missing HTTPX, Flask, Redis or SQLAlchemy | Install the matching source extra or `.[dev]`. Check the [version table](installation.md#choose-published-or-development) first. |
| `compare`, `doctor` or `export` is unknown | Those commands require development `0.1.0a3`; published `0.1.0a2` does not include them. |
| `list` is empty | Check `--store`. The HTTP demo uses `.rewind/demo`, while the default is `.rewind/snapshots`. |

## No recording appeared

Default retention keeps exceptions escaping the entry point and captured ASGI/WSGI response statuses of at least 500. An outbound HTTPX error response alone does not retain a normally returning callable. For that use case, choose an appropriate exception, duration condition or `Retention(always=True)`. Check your retention settings, whether capture is enabled, and `rewind.stats()` for admission or persistence failures. A background writer must be drained before process exit; see [operations](operations.md). An accepted submission is not proof that the file was saved.

## The recording is ineligible

Run `rewind inspect path/to/recording.rewind.json` and read `ineligible_reasons`. Common causes include policy-excluded values, redacted required observations, exceeded size/interaction budgets, unsupported driver operations, streaming bodies, or dependency calls from a child task.

Examples use `CapturePolicy.synthetic()` for known synthetic data. Do not enable it on arbitrary sensitive inputs just to force replay eligibility. Use the [supported adapter contracts](support-matrix.md) and an application-appropriate policy.

## Replay is incompatible

Strict replay compares declared sources, Python major/minor, installed supported adapter versions, application identity, policy and entry-point kind. Use the same application configuration and compatible environment that created the recording. Declared dependency installation ranges do not guarantee identical resolved versions.

If your intended change is to application source, choose [comparison](comparison.md) explicitly. It permits the source digest change only; it does not bypass all compatibility checks or allow unmatched live calls.

## Replay diverged

Check the number of consumed interactions and the bounded report detail. A changed request, SQL parameter, Redis command, source-method argument, call order or final result can cause divergence. Rewind does not silently call the real dependency when no recording matches.

Use `inspect --timeline` on development `0.1.0a3` to examine recorded dependency summaries. Function spans appear only if tracing was enabled when capturing; inspection cannot reconstruct missing spans afterward.

## Replay failed or timed out

Verify the `--app module:function` spelling and run from a location where that module is importable. The factory must return `ReplayTarget`. Avoid unwrapped service connections during module import or factory construction: the replay guard is already active. Check the adapter guide for where client/engine construction belongs.

The default timeout is 30 seconds. Increase `--timeout` only when your trusted local application needs more time. A Python audit guard is not an OS sandbox, and disabling it does not make unsupported replay safe or deterministic. See [security](../SECURITY.md).

## Test generation or import says operation failed

Choose a new `.py` output name if a test or its neighboring fixture already exists. Export similarly refuses existing destinations; import refuses duplicate snapshot IDs. Check path permissions and artifact validity. An exported `.rewind` archive must be imported before replay; direct replay expects the JSON recording.

## Get help

Open a [GitHub issue](https://github.com/iamjpsonkar/JaySoft-Rewind/issues) with the package/Python versions, integration and dependency versions, command, result status and a minimal synthetic reproduction. Review any recording before sharing. Use the [security reporting instructions](../SECURITY.md) for sensitive information.
