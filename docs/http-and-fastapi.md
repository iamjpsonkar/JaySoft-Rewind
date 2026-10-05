# HTTPX and FastAPI

[Documentation home](index.md) · [Quick start](getting-started.md) · [Synchronous HTTP and Flask](synchronous.md)

These async integrations are available in published `0.1.0a2` and the development version. For source examples, follow the [installation guide](installation.md).

## Connect your application

Use an explicitly wrapped HTTPX async transport and declare the source paths relevant to replay:

```python
from pathlib import Path

import httpx

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind


def replay_target():
    rewind = Rewind(
        application="checkout-demo",
        code_paths=[Path(__file__)],
        store=LocalStore(".rewind/snapshots"),
        policy=CapturePolicy.synthetic(),  # Known synthetic fixture data only.
    )

    async def checkout(order_id):
        async with httpx.AsyncClient(transport=rewind.httpx_transport()) as client:
            response = await client.get(f"https://provider.example/orders/{order_id}")
            return response.json()["payment_id"]

    return ReplayTarget(rewind, checkout)


# During capture, in your application's existing event loop:
# target = replay_target()
# result = await target.rewind.run(target.entrypoint, "order-demo")
```

This snippet uses a live transport during capture. Supply your controlled service or use `httpx.MockTransport` as in the runnable examples. Replay consumes recorded interactions without calling the wrapped transport.

For FastAPI, wrap the app with `rewind.asgi(app)` and return `ReplayTarget(rewind, wrapped_app, kind="asgi")`. Middleware does not intercept other clients or databases. Supported requests consume their bodies, emit buffered responses, and perform dependency calls sequentially in the owning task. Configure local startup dependencies inside the factory; the runner does not drive ASGI lifespan.

`Rewind.value(name, factory)` records explicit observations. `Retention` supports exceptions, status thresholds, duration thresholds, and `always=True`; defaults retain exceptions and HTTP statuses of at least 500. The existing `store=` capture path writes retained artifacts synchronously.

## Try the complete examples

From the installed source checkout, run:

```sh
artifact="$(python -m examples.http_failure)"
rewind replay "$artifact" --app examples.http_failure:replay_target
artifact="$(python -m examples.fastapi_failure)"
rewind replay "$artifact" --app examples.fastapi_failure:replay_target
```

The HTTP fixture reproduces a missing `payment_id` field. The FastAPI fixture reproduces an HTTP 502 after an upstream 503. Both use synthetic mock responses during capture. A `reproduced` report confirms the same failure occurred.

## Supported requests

HTTPX 0.28.x buffered request/response bodies and supported transport errors are covered. ASGI HTTP requests must consume their bodies and emit buffered responses. Child-task dependency calls, WebSockets, SSE, response streaming and lifespan replay are outside this contract. Independent requests can be captured concurrently. See the [support matrix](support-matrix.md) and [troubleshooting](troubleshooting.md).
