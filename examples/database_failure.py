"""Capture a SQLite transaction failure and replay without opening a database."""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind
from rewind.adapters.sqlalchemy import create_engine


def make_target(store: LocalStore | None = None) -> ReplayTarget:
    rewind = Rewind(
        application="rewind-database-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
    )

    async def operation() -> None:
        engine = create_engine(dependency="orders")
        try:
            with engine.begin() as connection:
                connection.execute(text("create table orders(id integer, status text)"))
                connection.execute(text("insert into orders values (7, 'pending')"))
                result = connection.execute(text("select id, status from orders"))
                row = tuple(result.one())
                raise RuntimeError("synthetic order could not be confirmed", row)
        finally:
            engine.dispose()

    return ReplayTarget(rewind, operation)


def replay_target() -> ReplayTarget:
    return make_target()


async def record(path: Path) -> Path:
    store = LocalStore(path)
    target = make_target(store)
    before = set(store.ids())
    try:
        await target.rewind.run(target.entrypoint)
    except RuntimeError:
        pass
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("demo did not produce exactly one recording")
    identifier = created.pop()
    if not store.load(identifier).complete:
        raise RuntimeError("database demo recording is incomplete")
    return path / f"{identifier}.rewind.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/database-demo"))
    print(asyncio.run(record(parser.parse_args().store)))


if __name__ == "__main__":
    main()
