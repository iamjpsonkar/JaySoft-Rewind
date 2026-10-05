# CLI reference

[Documentation home](index.md) · [Quick start](getting-started.md) · [Troubleshooting](troubleshooting.md)

Run `rewind --help` or `rewind COMMAND --help` for installed-version options. `python -m rewind` is equivalent and helps ensure you use the active Python environment.

This reference describes `0.1.0a3`. The earlier `0.1.0a2` alpha includes basic list, inspect, replay, delete and reproduction-test generation. Comparison, timeline inspection, archives, doctor and lookup by snapshot ID for replay/inspect/test were added in `0.1.0a3`.

## Command map

The examples below use `artifact` from the [quick start](getting-started.md), or replace it with an existing recording path. A `SNAPSHOT_ID` is the 32-character ID shown by `list`; it is not a literal filename.

| Command | Example | Purpose |
| --- | --- | --- |
| `list` | `rewind list --store .rewind/demo` | List summaries in a store |
| `inspect` | `rewind inspect "$artifact"` | Validate and summarize without running application code |
| `inspect --timeline` | `rewind inspect "$artifact" --timeline` | Include optional spans and dependency timing |
| `replay` | `rewind replay "$artifact" --app examples.http_failure:replay_target` | Strict replay in a fresh process |
| `compare` | `rewind compare "$artifact" --app examples.http_failure:replay_target` | Explicitly permit changed source; default to the recorded outcome |
| `test` | `rewind test "$artifact" --app examples.http_failure:replay_target --output test_reproduction.py` | Generate a reproduction test and copy its fixture |
| `export` | `rewind export "$artifact" -o failure.rewind` | Create a bounded portable archive |
| `import` | `rewind import failure.rewind --store .rewind/imported` | Validate and save an archive's recording |
| `doctor` | `rewind doctor` | Show runtime and installed optional integrations |
| `delete` | `rewind delete SNAPSHOT_ID --store .rewind/demo` | Delete exactly one recording by ID |

## Paths, IDs and stores

The default store is `.rewind/snapshots`. The HTTP example explicitly uses `.rewind/demo`. `--store` selects the location for listing, importing, deleting and resolving IDs. A supplied file path is used directly.

```sh
rewind inspect SNAPSHOT_ID --store .rewind/demo
rewind replay SNAPSHOT_ID --store .rewind/demo --app examples.http_failure:replay_target
```

Test generation and export refuse to overwrite output files. Import refuses to replace an existing ID. Exported archives carry recording data only; use `import` before replaying an archive.

## Replay options

| Option | Default | Meaning |
| --- | --- | --- |
| `--app module:function` | Required | Local factory returning `ReplayTarget` |
| `--timeout SECONDS` | `30` | Positive finite worker timeout |
| `--isolation python-guard` | `python-guard` | Selected Python audit events blocked before application import |
| `--isolation adapter-only` | Opt-in | Adapter matching without the Python guard |

`adapter-only` removes a protection and is not a workaround for unsupported dependencies. Neither profile is an OS sandbox; see [security](../SECURITY.md). Your factory module must be importable from the replay environment.

## Comparison and regression tests

`compare` permits a changed source digest while retaining runtime, dependency, policy, application identity and ordered-interaction checks. It reports `mode: comparison`, not strict reproduction.

Use `--expected-return JSON` to supply a desired return value, including `null` for `None`. Use `--expected-outcome JSON` for a canonical typed outcome object. Those flags are mutually exclusive. A generated test accepts them only with `--compare-code`:

```sh
# Replace these placeholders with your recording, factory and intended return value.
rewind compare recording.rewind.json --app myapp:replay_target --expected-return '42'
rewind test recording.rewind.json --app myapp:replay_target --compare-code --expected-return '42' --output test_fixed.py
```

See the [comparison guide](comparison.md) for typed outcomes and working examples.

## Results and exit codes

| Result | Exit code | Next action |
| --- | ---: | --- |
| `reproduced` (replay) | 0 | The recorded observations and outcome matched |
| `matched` (comparison) | 0 | The explicitly selected comparison expectation matched |
| `diverged` | 1 | Check changed inputs, dependency order and outcome |
| `ineligible` | 2 | Inspect omitted data, unsupported operations and capture limits |
| `incompatible` | 2 | Restore matching sources/environment or explicitly select comparison for a source change |
| `replay_error` | 3 | Check factory imports, guard violations, timeout or worker execution |

Other completed commands return `0`; handled file/data/argument-value failures return `3` with a generic error. Command-line parser errors return `2`. Error messages deliberately avoid echoing recorded values. [Troubleshooting](troubleshooting.md) provides next steps.

## Explore a snapshot

```sh
rewind explore snapshot.rewind.json --output report.html
```

Open the generated standalone HTML file in your browser to explore the request,
dependency observations, outcome and optional timeline. The command validates data
without importing application code. See [the explorer guide](explorer.md).
