"""Capture a synthetic relational failure; configure a disposable database first."""

import argparse
import json
import os
from pathlib import Path

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind
from rewind.adapters.mysql import create_engine as mysql_engine
from rewind.adapters.postgres import create_engine as postgres_engine


def make_target(driver: str, store: LocalStore | None = None) -> ReplayTarget:
    from sqlalchemy import text

    rewind = Rewind(
        application=f"rewind-{driver}-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
    )

    def operation() -> None:
        if driver == "postgres":
            engine = postgres_engine(os.environ.get("REWIND_POSTGRES_DSN", ""))
        elif driver == "mysql":
            engine = mysql_engine(
                connect_args=json.loads(os.environ.get("REWIND_MYSQL_CONFIG", "{}"))
            )
        else:
            raise ValueError("unsupported database driver")
        try:
            with engine.connect() as connection:
                row = tuple(connection.execute(text("select 7 as item_id, 12.50 as amount")).one())
                raise RuntimeError("synthetic report generation failed", row)
        finally:
            engine.dispose()

    return ReplayTarget(rewind, operation, kind="callable_sync")


def postgres_target() -> ReplayTarget:
    return make_target("postgres")


def mysql_target() -> ReplayTarget:
    return make_target("mysql")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", choices=("postgres", "mysql"), required=True)
    parser.add_argument("--store", type=Path, default=Path(".rewind/relational-demo"))
    args = parser.parse_args()
    store = LocalStore(args.store)
    target = make_target(args.driver, store)
    before = set(store.ids())
    try:
        target.rewind.run_sync(target.entrypoint)
    except RuntimeError:
        pass
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("demo did not produce one recording")
    identifier = created.pop()
    if not store.load(identifier).complete:
        raise RuntimeError("demo recording is incomplete")
    print(store.path / f"{identifier}.rewind.json")


if __name__ == "__main__":
    main()
