"""One synthetic failure spanning SQLite, Redis, HTTP and explicit value sources."""

import argparse
import asyncio
from pathlib import Path

import httpx
from sqlalchemy import text

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind
from rewind.adapters.redis import AsyncRecordingRedis
from rewind.adapters.sqlalchemy import create_engine


class FixtureCache:
    async def get(self, key: str) -> bytes:
        return b"fixture-plan"


def make_target(store: LocalStore | None = None) -> ReplayTarget:
    rewind = Rewind(
        application="combined-failure",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
    )
    cache = AsyncRecordingRedis(FixtureCache() if store is not None else None, dependency="plans")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"state": "pending"}))

    async def operation() -> None:
        engine = create_engine(dependency="orders")
        try:
            with engine.begin() as connection:
                connection.execute(text("create table orders(id integer, plan text)"))
                plan = (await cache.get("order-plan")).decode()
                connection.execute(
                    text("insert into orders values (:id, :plan)"), {"id": 7, "plan": plan}
                )
                order_id = connection.execute(text("select id from orders")).scalar_one()
                request_id = str(rewind.sources.uuid4())
                region = rewind.sources.getenv("REWIND_DEMO_REGION", "fixture")
                await rewind.sources.sleep(0)
                async with httpx.AsyncClient(transport=rewind.httpx_transport(transport)) as client:
                    response = await client.post(
                        "https://provider.example/confirm",
                        json={"order_id": order_id, "request_id": request_id, "region": region},
                    )
                    response.json()["confirmation_id"]
        finally:
            engine.dispose()

    return ReplayTarget(rewind, operation)


def replay_target() -> ReplayTarget:
    return make_target()


async def record(path: Path) -> Path:
    store = LocalStore(path)
    before = set(store.ids())
    target = make_target(store)
    try:
        await target.rewind.run(target.entrypoint)
    except KeyError:
        pass
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("combined demo did not persist one snapshot")
    identifier = created.pop()
    if not store.load(identifier).complete:
        raise RuntimeError("combined demo is incomplete")
    return path / f"{identifier}.rewind.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/combined-demo"))
    print(asyncio.run(record(parser.parse_args().store)))
