# Capture a function or class when your condition matches

[Documentation home](index.md) · [Explore snapshots](explorer.md)

Import `capture`, add it above your function or class, and define when to save a
snapshot. Your application calls the decorated code normally. Rewind records the
supported inputs and outcome; after execution, your condition decides whether
to keep the recording.

```python
from rewind import Call, capture


def save_this_call(call: Call) -> bool:
    if call.error is not None:
        return True
    return call.result > 10_000


@capture(condition=save_this_call, store=".rewind/snapshots")
def calculate_total(order):
    return order["quantity"] * order["unit_price"]
```

A snapshot can be opened to understand the inputs and error. Replay runs the
same decorated function again with its recorded inputs, in a fresh Python
process. You do not need to write a replay factory.

## 1. Install the decorator version

This API is introduced in **0.2.0a2**. Install it in your application's virtual
environment:

```bash
python -m pip install 'jaysoft-rewind==0.2.0a2'
```

For development from a source checkout instead:

```bash
python -m pip install -e /path/to/JaySoft-Rewind
```

The package is named `jaysoft-rewind`; Python imports and terminal commands use
`rewind`. No web server is needed for the following walkthrough.

## 2. Add the decorator to an importable module

Create a file named **`payment_logic.py`** in your working directory:

```python
from rewind import Call, capture


def save_failed_calls(call: Call) -> bool:
    return call.error is not None


@capture(condition=save_failed_calls, store=".rewind/snapshots")
def calculate_total(order):
    return order["quantity"] * order["unit_price"]
```

Keep the function in an importable module. The commands below import it instead
of defining it in a terminal, notebook, or `__main__` script, so the replay
worker can locate the same code.

## 3. Run your application normally

From the directory containing `payment_logic.py`, run a successful call:

```bash
python -c 'from payment_logic import calculate_total; print(calculate_total({"quantity": 2, "unit_price": 100}))'
```

It prints `200`. The condition is false, so no snapshot is saved.

Now call it with the price missing:

```bash
python -c 'from payment_logic import calculate_total; calculate_total({"quantity": 2})'
```

The function raises `KeyError: 'unit_price'`. The condition matches, so Rewind
saves a snapshot under `.rewind/snapshots`. The original exception still reaches
the caller; recording does not swallow or replace it.

In an existing backend, put the decorator on the function your handler calls
and send your usual HTTP request. There is no special request header or separate
Rewind server to start.

## 4. Find and read the snapshot

```bash
rewind list --store .rewind/snapshots
```

Copy the listed snapshot ID into this command:

```bash
ARTIFACT=.rewind/snapshots/PASTE_SNAPSHOT_ID.rewind.json
rewind inspect "$ARTIFACT"
rewind explore "$ARTIFACT" --output failure-report.html
```

Open `failure-report.html` in your browser. On macOS, you can run
`open failure-report.html`. The report shows the captured input and outcome;
this example records `quantity: 2` and the missing-price exception. Choose a new
output filename when creating another report.

## 5. Reuse the snapshot to reproduce the error

Keep the same source code and Python environment, then run:

```bash
rewind replay "$ARTIFACT" --app payment_logic:calculate_total
```

A matching replay reports `"status": "reproduced"`. That means the function ran
again and raised the same recorded error. It does not mean the bug was fixed.
The replay does not call your retention condition or save another snapshot.

You can also invoke replay from Python:

```python
from payment_logic import calculate_total

report = calculate_total.rewind.replay(
    ".rewind/snapshots/PASTE_SNAPSHOT_ID.rewind.json"
)
print(report.status)
```

This convenience method also uses a fresh worker. Changed code or runtime can
make a snapshot incompatible; use the [comparison workflow](comparison.md) for
intentional changes to application behavior.

## 6. Choose your condition

The condition runs **after the call**, so it can inspect the return value,
exception, duration, and named arguments. A normal Python function gives you a
condition block of any complexity:

```python
def save_interesting_calls(call: Call) -> bool:
    if call.error is not None:
        return True
    if call.duration >= 0.5:
        return True
    order = call.arguments["order"]
    return order.get("quantity", 0) > 100
```

| Field | Meaning |
| --- | --- |
| `call.arguments` | Arguments bound to the function's parameter names |
| `call.args`, `call.kwargs` | Positional and keyword arguments |
| `call.result` | Returned value, or `None` when the call raised |
| `call.error` | Raised exception, or `None` on success |
| `call.duration` | Execution duration in seconds |
| `call.status` | Recognized response status, when available |

The condition context exists in memory. Its raw objects are not automatically
copied into the saved artifact. Use a synchronous predicate returning a boolean;
async predicates are rejected. If a predicate raises, the original application
result or exception is preserved, the snapshot is not retained, and
`calculate_total.rewind.stats()["condition_errors"]` increases.

For simple rules, a bounded expression is supported:

```python
@capture(condition="exception or duration >= 500ms")
def calculate_total(order):
    return order["quantity"] * order["unit_price"]
```

Expressions support `exception`, `status`, `duration`, `always`, `never`, and
`and`/`or`/`not` with parentheses. They do not execute arbitrary Python.

To retain every admitted call, including successes, omit the condition:

```python
@capture
def calculate_total(order):
    return order["quantity"] * order["unit_price"]
```

`@capture()` and `@capture(condition=None)` have the same retention behavior.
Storage and capture limits still apply. `async def` functions also work; call
them with `await` as usual.

## Decorate a class

Add this class to an importable module such as `payment_logic.py`:

```python
@capture(condition=save_failed_calls, store=".rewind/snapshots")
class Checkout:
    def __init__(self):
        self.calls = 0

    def total(self, order):
        self.calls += 1
        return order["quantity"] * order["unit_price"]
```

Call it normally:

```python
checkout = Checkout()
checkout.total({"quantity": 2})
```

The class decorator captures supported public methods declared on that class;
it leaves inherited methods, private methods and construction unchanged.
For an ordinary data-only instance, the recording includes its state
before the method ran, so replay restores `self.calls` before rerunning `total`.
Replay creates a fresh instance without calling your constructor or modifying
the original instance:

```bash
rewind replay "$ARTIFACT" --app payment_logic:Checkout.total
```

Use the snapshot ID from this method call. You can also place `@capture(...)`
directly on a method when you want to record only that method.

Choose either class decoration or individual method decoration; applying both
to the same method is rejected. Nested decorated calls share the outer capture:
only the outer call's condition controls its snapshot. Individual nested calls
do not create separate snapshots or independently evaluate their conditions.

## What can be reproduced

This decorator captures supported argument values, ordinary instance data, and
the return value or exception. It does **not** automatically record every
database query, network request, clock read, or random value inside your code.
Use the supported [dependency adapters](support-matrix.md) and
[time and randomness sources](sources.md) for those observations. Guarded replay
blocks supported categories of uninstrumented external operations; a blocked
operation is a replay failure, not a successful reproduction.

Custom framework request objects, live clients, locks and arbitrary object graphs
are not automatically serializable. Class-level data attributes, including
constants, are currently marked unsupported; use ordinary instance attributes
for replayable class state. Mutable alias identity is not preserved: two arguments
referring to the same list are restored by value. A class
decorator does not promise reconstruction of every Python class. Unsupported or
redacted values can make a saved snapshot incomplete; inspect its eligibility
reasons before attempting strict replay. Rewind does not pickle application
objects.

Sensitive field names, including named positional arguments such as `password`,
are redacted. Redaction can prevent exact replay. Review the values your function
accepts and returns, configure `policy=` when needed, and keep snapshot storage
private. Use `code_paths=` to include additional application source paths in the
compatibility fingerprint when the function depends on other modules.
