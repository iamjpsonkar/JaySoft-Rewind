"""Record a missing provider field, then replay it in a fresh process."""

import argparse
import asyncio
from pathlib import Path

import httpx

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind


def make_target(store: LocalStore | None = None) -> ReplayTarget:
    rewind = Rewind(
        application="rewind-http-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
    )

    async def checkout(order_id: str) -> str:
        # This synthetic response models a provider omitting a required field.
        upstream = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"order_id": order_id})
        )
        async with httpx.AsyncClient(
            transport=rewind.httpx_transport(upstream, dependency="gateway")
        ) as client:
            response = await client.get(f"https://gateway.example/orders/{order_id}")
            return response.json()["payment_id"]

    return ReplayTarget(rewind, checkout)


def replay_target() -> ReplayTarget:
    return make_target()


async def record(path: Path) -> Path:
    store = LocalStore(path)
    target = make_target(store)
    before = set(store.ids())
    try:
        await target.rewind.run(target.entrypoint, "order-demo")
    except KeyError:
        pass
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("demo did not produce exactly one recording")
    return path / f"{created.pop()}.rewind.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/demo"))
    args = parser.parse_args()
    print(asyncio.run(record(args.store)))


if __name__ == "__main__":
    main()
