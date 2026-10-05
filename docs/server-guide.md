# How to use Rewind in your server

[Documentation home](index.md) · [Snapshot explorer](explorer.md)

Add Rewind to the server, send an ordinary HTTP request, and keep its recording.
Later, Rewind runs the **same server handler** in a fresh process with the recorded
request and dependency observations. You can reproduce a failure after the
original provider or server has stopped.

This tutorial uses a small FastAPI application. Its `/quote` handler asks a
catalog provider for a price. The provider returns HTTP 200 with a JSON object
missing `unit_price`, so the handler raises `KeyError` and FastAPI returns HTTP
500. The provider is an explicit local fixture; no external account is needed.

## 1. Start the instrumented server

Run these commands from a checkout of this repository; the `examples` directory
is repository content, not an application module installed by the wheel:

```bash
git clone https://github.com/iamjpsonkar/JaySoft-Rewind.git
cd JaySoft-Rewind
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[fastapi]'
python -m examples.server_demo --store .rewind/server-demo --port 8000
```

Keep this terminal running. The example uses Uvicorn's
[programmatic server interface](https://www.uvicorn.org/deployment/).

**No `--when` means record every admitted HTTP request in this example**, including
successful requests. It explicitly sets `Retention(always=True)`. A bare
`Rewind(...)` instance otherwise defaults to retaining exceptions and HTTP 5xx
responses. Storage, size limits, unsupported interactions, and capture admission
still apply; always-retain does not promise unlimited or complete recording.

## 2. Send an ordinary HTTP request

In a second terminal, activate the same virtual environment and run:

```bash
curl -i http://127.0.0.1:8000/quote \
  -H 'Content-Type: application/json' \
  -d '{"sku":"demo-widget","quantity":2}'
```

The response is `500 Internal Server Error`, and the server terminal shows the
missing `unit_price` field. This error comes from the handler using the malformed
provider response. The client does not need a Rewind library or special header.

For a successful request:

```bash
curl -i http://127.0.0.1:8000/health
```

Both requests are retained when no condition is configured. The health handler
consumes its request body, even when empty, so replay can verify that the recorded
input was consumed. Middleware does not drain unread bodies on the app's behalf.

## 3. Find and explore the snapshot

```bash
rewind list --store .rewind/server-demo
```

Choose the `/quote` recording: it has one dependency interaction, whereas
`/health` has zero. Its file is `.rewind/server-demo/<snapshot-id>.rewind.json`.
Replace `PASTE_SNAPSHOT_ID` below with the `id` from the list:

```bash
ARTIFACT=.rewind/server-demo/PASTE_SNAPSHOT_ID.rewind.json
rewind inspect "$ARTIFACT"
rewind explore "$ARTIFACT" --output report.html
```

Open `report.html` in a browser. The report lets you inspect the recorded request,
dependency calls, and handler outcome. The failure recording contains:

- The inbound `POST /quote` scope and JSON request body.
- One `http.request` interaction for dependency `catalog`, including the outbound
  request and the provider's malformed HTTP 200 response.
- The final HTTP 500 response and the handler's `KeyError('unit_price')`.
- A bounded `quote.handler` timeline showing handler entry and exception.

The short inspector can label the outcome `return` because the outer ASGI result
is an HTTP response. The nested response outcome also preserves the exception;
use the explorer to inspect those details.

Check completeness before replay. Incomplete recordings remain useful evidence
but cannot support strict reproduction. The
[snapshot format](snapshot-format.md) explains the underlying artifact fields.

## 4. Replay the same handler with the provider unavailable

Stop the demo server with Ctrl-C. Then run:

```bash
rewind replay "$ARTIFACT" \
  --app examples.server_demo:replay_target
```

A successful reproduction reports `"status": "reproduced"` and consumes the one
recorded provider interaction. The local factory rebuilds the same FastAPI routes.
Rewind supplies the recorded inbound request, the handler executes its normal
HTTPX call, and the adapter supplies the recorded provider response. The same
missing-field access fails again. The replay factory installs a provider fixture
that raises if a live provider call occurs.

Reproduction means the original failure was reproduced; it does not mean the
application bug was fixed. Strict replay also checks code, runtime, dependency,
capture-policy, and interaction compatibility. After changing the handler, use
the separate [comparison workflow](comparison.md) with an explicit desired
outcome when appropriate. The Python guard is an application-level guard;
[replay isolation guidance](comparison.md#execution-and-isolation) explains its
limits and stronger network isolation.

## 5. Retain only requests matching a condition

Restart the server with one of these configurations:

```bash
# Exceptions or HTTP server errors:
python -m examples.server_demo --when 'exception or status >= 500'

# Responses taking at least 250 milliseconds:
python -m examples.server_demo --when 'duration >= 250ms'

# Failed requests that also exceeded a duration threshold:
python -m examples.server_demo --when '(exception or status >= 500) and duration > 1s'
```

The condition controls retention after execution. The HTTP handler still runs
normally when a request is not retained. Use `exception`, `status`, and `duration`
with comparisons and `and`, `or`, `not`, or parentheses; durations accept `ms`,
`s`, or plain seconds. Conditions are a bounded grammar, not arbitrary Python.
An unhandled FastAPI exception can produce both an HTTP 500 and a captured
exception. A handled error response may only match its status predicate.

## Add the integration to your own application

Once your `api` FastAPI application and routes exist, the server setup is small:

```python
from rewind import CapturePolicy, LocalStore, Retention, Rewind
rewind = Rewind(application="catalog-service", code_paths=["app"],
                store=LocalStore(".rewind/snapshots"),
                policy=CapturePolicy.synthetic(), retain=Retention(always=True))
app = rewind.asgi(api)
```

`code_paths` must cover your handler code and application modules that influence
its behavior. Serve `app` instead of `api`. This snippet enables body and value
capture for a **synthetic local tutorial**. Before using real traffic, select a
[capture policy](portable-tooling.md#application-specific-redaction) suitable for
your data; default privacy restrictions
can deliberately make an artifact ineligible when required values are omitted.

Wrap each dependency your handler uses. For HTTPX, construct the client inside
the handler or your usual client factory using the Rewind transport:

```python
async with httpx.AsyncClient(transport=rewind.httpx_transport(
    dependency="catalog"
)) as client:
    response = await client.get("https://catalog.example/price")
```

The ASGI wrapper captures the request and response; the transport captures the
outbound observation. Other supported boundaries have explicit adapters for
[databases](database.md), [external database drivers](external-databases.md),
and [Redis](redis.md). Unwrapped dependencies are not automatically recorded.
Replay is fail-closed for unmatched supported interactions.

Finally, expose a local factory returning
`ReplayTarget(rewind, app, kind="asgi")`. Reuse your application builder so it
constructs the same routes and dependency wrappers, without starting a server,
opening live connections, or requiring production credentials during import.
The complete [server example](../examples/server_demo.py) shows this arrangement
and a replay-only provider that forbids live access. It enables optional
`TraceConfig(enabled=True)` and decorates the handler with `@trace` for a
bounded function timeline. This does not record every local variable or line.

Buffered, single-task HTTP requests are the supported starting point. WebSockets,
streaming responses, unread request bodies, and detached dependency work can
make capture unsupported or incomplete. Read the
[framework guide](http-and-fastapi.md) before expanding beyond this example.
