# Synchronous Python, HTTPX, and Flask

[Documentation home](index.md) · [Quick start](getting-started.md)

Synchronous capture uses the same bounded snapshot, privacy policy, retention,
background persistence, and strict observation matching as async capture.

```python
import httpx
from rewind import CapturePolicy, LocalStore, Rewind

rewind = Rewind(
    application="checkout",
    code_paths=[__file__],
    store=LocalStore(".rewind/snapshots"),
    policy=CapturePolicy.synthetic(),  # controlled fixture inputs only
)

client = httpx.Client(transport=rewind.httpx_sync_transport())

@rewind.capture(when="exception or duration > 2s")
def checkout(order_id):
    response = client.get("https://gateway.example/orders/" + order_id)
    response.raise_for_status()
    return response.json()

# Equivalently: rewind.run_sync(checkout, "example-order")
# Replay a saved snapshot with rewind.replay_sync(snapshot, checkout).
```

`capture()` selects a synchronous or asynchronous wrapper when decorating the
function. `run_sync()` and `replay_sync()` never create an event loop. Nested
captures join the current capture. A sync dependency called inside the owning
async task also joins that request. Each execution belongs to both its original
thread and its original asyncio task (if present); copying context into another
thread does not make concurrent observations deterministic. Such capture is
ineligible, and replay rejects it.

Decorator `when=` conditions are parsed once and belong to that decorator;
they do not mutate `rewind.retain` or another request's retention. Status-based
conditions require a framework response status; ordinary callable capture has
only exception and duration information. Synchronous HTTPX uses the same strict
`http.request` protocol as its async transport. Replay returns recorded responses
or supported HTTPX exceptions before constructing or calling a live transport.
Partial or compressed response observations remain explicitly ineligible.

## Flask and WSGI

```python
from flask import Flask

app = Flask(__name__)
# Configure routes normally, then wrap the WSGI entry point.
app.wsgi_app = rewind.wsgi(app.wsgi_app)
```

`rewind.wsgi(app)` also accepts a plain WSGI callable. It follows application
request reads, forwards `start_response` and its optional `exc_info`, preserves
legacy `write()` calls, yields response chunks lazily, and forwards iterable
`close()`. It never reads the request or drains the response ahead of the server.
Successful bounded sequential responses can contain multiple chunks. The
recorded outcome preserves whether each output came from `write()` or iteration.

Artifacts finalize when the server calls the response iterable's `close()`.
That includes interactions performed by application cleanup. WSGI servers must
close returned iterables; manual callers must do the same in a `finally` block.
The wrapper does not auto-close or drain the application on exhaustion. Closing
before exhaustion preserves the application's behavior and marks capture
ineligible. A response that is never closed retains its active capture reservation.

Request `read`, `readline`, `readlines`, iteration, and `readinto` observations
are matched in sequence with all other dependencies. Read arguments and bytes
must match. Replay supplies an observation-backed stream and does not open a
request socket. Supported input exceptions are `OSError`, `ValueError`, and
`StopIteration`; other input behavior becomes diagnostic-only. Metadata captures
standard CGI/WSGI fields, HTTP headers, and `RAW_URI`/`REQUEST_URI`, with policy
redaction. Additional environment extensions, custom iterable length behavior,
repeated `start_response`, and input stream extensions are explicitly ineligible.

Body byte and interaction limits apply across the exchange. Sensitive headers,
query parameters, and JSON fields are redacted. A JSON body read in fragments
that cannot be independently validated becomes ineligible rather than exposing
uninspected fragments. Error-stream writes are not part of the replay oracle;
replay provides a local in-memory error stream. Server scheduling, file-wrapper
optimizations, and external WSGI server behavior are outside the supported scope.

Use `rewind.replay_wsgi(snapshot, app)` for in-process WSGI replay. For isolated
CLI replay, export a factory returning `ReplayTarget(rewind, app, kind="wsgi")`.
Synchronous callable factories use `kind="callable_sync"`. In-process replay
intercepts the installed adapters; the isolated runner additionally applies its
documented Python network guard.

```sh
python -m examples.flask_failure --store .rewind/flask-demo
```

The implementation follows the [WSGI interface specification](https://peps.python.org/pep-3333/),
[HTTPX custom transport APIs](https://www.python-httpx.org/advanced/transports/),
and [Flask's WSGI lifecycle](https://flask.palletsprojects.com/en/stable/lifecycle/).
