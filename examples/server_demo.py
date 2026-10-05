"""Run an instrumented server and replay its handler from recorded dependencies."""

import argparse
from pathlib import Path

import httpx
from fastapi import FastAPI, Request

from rewind import CapturePolicy, Condition, LocalStore, ReplayTarget, Retention, Rewind


def malformed_provider(request: httpx.Request) -> httpx.Response:
    """A deterministic provider fixture: its successful JSON omits unit_price."""
    return httpx.Response(200, json={"sku": "demo-widget", "currency": "USD"})


def forbidden_provider(request: httpx.Request) -> httpx.Response:
    raise AssertionError("replay attempted to call the live provider")


def make_target(
    store: LocalStore | None = None,
    *,
    when: str | None = None,
    replay: bool = False,
) -> ReplayTarget:
    rewind = Rewind(
        application="rewind-server-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
        retain=Condition.parse(when) if when is not None else Retention(always=True),
    )
    api = FastAPI()
    provider = forbidden_provider if replay else malformed_provider

    @api.get("/health")
    async def health(request: Request):
        # Consume the request in the handler; recording never drains it for us.
        await request.body()
        return {"status": "ok"}

    @api.post("/quote")
    async def quote(request: Request):
        order = await request.json()
        async with httpx.AsyncClient(
            transport=rewind.httpx_transport(httpx.MockTransport(provider), dependency="catalog"),
            headers={"accept-encoding": "identity"},
        ) as client:
            response = await client.get(
                "https://catalog.example/price", params={"sku": order["sku"]}
            )
            response.raise_for_status()
            price = response.json()
        # A real handler defect: a 200 response does not guarantee this field.
        return {"total": order["quantity"] * price["unit_price"]}

    return ReplayTarget(rewind, rewind.asgi(api), kind="asgi")


def replay_target() -> ReplayTarget:
    """Build the same routes while forbidding any live provider observation."""
    return make_target(replay=True)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/server-demo"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--when", help="retain matching requests, for example 'exception or status >= 500'"
    )
    args = parser.parse_args()
    target = make_target(LocalStore(args.store), when=args.when)
    print(f"Recording requests to {args.store}", flush=True)
    uvicorn.run(target.entrypoint, host=args.host, port=args.port, loop="asyncio")


if __name__ == "__main__":
    main()
