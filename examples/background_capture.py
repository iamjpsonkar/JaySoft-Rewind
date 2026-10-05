"""Record a synthetic failure with bounded background persistence and replay it."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind, replay_file
from rewind.persistence import BackgroundWriter


def make_target(writer: BackgroundWriter | None = None) -> ReplayTarget:
    rewind = Rewind(
        application="rewind-background-demo", code_paths=[__file__], writer=writer,
        policy=CapturePolicy.synthetic(),
    )

    async def checkout() -> None:
        receipt = {"id": rewind.sources.uuid4(), "created_at": rewind.sources.time_ns()}
        raise RuntimeError("synthetic checkout failure", receipt)

    return ReplayTarget(rewind, checkout)


def replay_target() -> ReplayTarget:
    return make_target()


async def record(path: Path) -> tuple[Path, dict[str, int | bool]]:
    store = LocalStore(path)
    writer = BackgroundWriter(store, max_items=8, max_bytes=2 * 1024 * 1024)
    target = make_target(writer)
    before = set(store.ids())
    try:
        try:
            await target.rewind.run(target.entrypoint)
        except RuntimeError:
            pass
        if not await target.rewind.aflush(timeout=5):
            raise RuntimeError("background capture flush timed out")
    finally:
        report = await target.rewind.aclose(timeout=5)
    if not report.drained or target.rewind.stats()["persistence_failed"]:
        raise RuntimeError("background capture did not persist successfully")
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("demo did not produce exactly one artifact")
    return path / f"{created.pop()}.rewind.json", target.rewind.stats()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/background-demo"))
    args = parser.parse_args()
    artifact, stats = asyncio.run(record(args.store))
    report = replay_file(artifact, "examples.background_capture:replay_target")
    print(json.dumps({"artifact": str(artifact), "stats": stats, "replay": asdict(report)}))
    if not report.reproduced:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
