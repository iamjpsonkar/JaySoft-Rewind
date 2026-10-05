# Compare changed code against a recording

[Documentation home](index.md) · [Quick start](getting-started.md)

Strict `replay_file` still requires the original declared source fingerprint.
Use the separate `compare_file` API when intentionally testing changed local
application code against the same dependency observations:

```python
from rewind.comparison import compare_file

report = compare_file("failure.rewind.json", "my_application:replay_target")
assert report.matched, report.to_dict()
```

The report is labeled `mode="comparison"`, with status `matched` or `diverged`,
and `code_changed` reports whether the declared source digest differs. It never
reports `reproduced` or exposes a `reproduced` property. A match against a recorded
exception means the exception still occurs; it does not mean a bug is fixed.

Only the source digest can differ. Application name, Python version, dependency
versions, capture policy, and entry-point kind must still match. Every dependency
call retains strict argument, order, and consumption checks. Added or missing
calls diverge even when a supplied expected outcome would otherwise match.
There is no live dependency fallback.

CLI equivalents:

```sh
rewind compare failure.rewind.json --app my_application:replay_target
rewind compare failure.rewind.json --app my_application:replay_target --expected-return '{"state":"confirmed"}'
rewind test failure.rewind.json --app my_application:replay_target --compare-code --expected-return '{"state":"confirmed"}' --output tests/test_fixed.py
```

`--expected-outcome` accepts canonical typed outcome JSON for bytes or exceptions.
Expected outcomes on `rewind test` require `--compare-code`; ordinary reproduction
tests retain the recorded oracle. CLI exit 0 means `matched` for comparison, with
the mode explicitly present in its JSON report.

## Specify the desired behavior

After changing the application, supply the expected return value yourself:

```python
report = compare_file(
    "failure.rewind.json",
    "my_application:replay_target",
    expected_return={"state": "confirmed", "order_id": 7},
)
assert report.matched, report.to_dict()
assert report.expected_source == "developer"
```

The default is the recorded outcome (`expected_source="recorded"`). An explicit
`expected_return=None` expects a successful return of `None`; omitting the
argument retains the recorded outcome. The API never invents a successful
return value from a failure artifact.

For a typed value or an expected exception, provide a canonical outcome instead:

```python
from rewind.codecs import encode
from rewind import Limits

expected = {
    "kind": "exception",
    "type": "builtins.ValueError",
    "args": encode(("order cannot be confirmed",), Limits()),
}
report = compare_file(
    "failure.rewind.json", "my_application:replay_target", expected_outcome=expected,
)
```

A canonical return outcome is `{"kind": "return", "value": encode(value, Limits())}`.
For ASGI/WSGI response comparisons use the complete supported response outcome,
including status, headers, and body. Supplying only a desired status would leave
other response behavior unverified, so the API does not infer those fields.
`expected_return` and `expected_outcome` are mutually exclusive. Canonical
outcomes are bounded data, not expressions or instructions to import classes.

## Generate a regression test

```python
from rewind.comparison import generate_comparison_test

generate_comparison_test(
    "failure.rewind.json",
    "my_application:replay_target",
    "tests/test_confirm_order.py",
    expected_return={"state": "confirmed", "order_id": 7},
)
```

This writes a deterministic test and a byte-for-byte artifact copy beside it.
The test embeds the developer's expected outcome and asserts `report.matched`.
Both files are created exclusively with owner-only permissions; existing files
are never overwritten. The original artifact is never edited. Without an
explicit outcome, the generated test is labeled a comparison against the
recorded outcome, including a recorded failure.

## Execution and isolation

Each comparison runs in a fresh process with a finite parent-enforced timeout.
Malformed or incomplete artifacts are rejected before application imports. The
Python guard is installed before the trusted local factory is imported and
checks for blocked operations even if application code catches the guard error.
The guard also blocks direct `sqlite3.connect` calls, preventing changed code
from opening a SQLite database outside the adapter.
The source digest and explicit expected outcome are changed only in an
in-memory snapshot copy. The capture artifact on disk stays unchanged.

Factories are selected by the developer using `module:function`, never by
artifact content. Supported target kinds are `callable`, `callable_sync`, `asgi`,
and `wsgi`. Code must continue to use Rewind adapters for dependency observations;
the Python guard is not an operating-system or native-code sandbox. A matched
comparison only establishes behavior for the recorded input and observations.
