"""Capture a buffered FastAPI POST and replay its recorded 502 response."""

import argparse
import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind


def make_target(store: LocalStore | None = None) -> ReplayTarget:
    rewind = Rewind(
        application="rewind-fastapi-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
    )
    app = FastAPI()

    @app.post("/checkout")
    async def checkout(request: Request):
        body = await request.json()
        upstream = httpx.MockTransport(
            lambda req: httpx.Response(503, json={"error": "unavailable"})
        )
        async with httpx.AsyncClient(
            transport=rewind.httpx_transport(upstream, dependency="gateway")
        ) as client:
            response = await client.post("https://gateway.example/orders", json=body)
        return JSONResponse(
            {"error": "provider unavailable", "provider_status": response.status_code},
            status_code=502,
        )

    return ReplayTarget(rewind, rewind.asgi(app), kind="asgi")


def replay_target() -> ReplayTarget:
    return make_target()


async def record(path: Path) -> Path:
    store = LocalStore(path)
    target = make_target(store)
    before = set(store.ids())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=target.entrypoint), base_url="http://local"
    ) as client:
        response = await client.post("/checkout", json={"order_id": "order-demo"})
        assert response.status_code == 502
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("demo did not produce exactly one recording")
    return path / f"{created.pop()}.rewind.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/fastapi-demo"))
    args = parser.parse_args()
    print(asyncio.run(record(args.store)))


if __name__ == "__main__":
    main()
