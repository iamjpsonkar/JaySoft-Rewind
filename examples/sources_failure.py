"""Record a synthetic booking failure with time, UUID, random, and wait observations."""

import argparse
import asyncio
from datetime import UTC
from pathlib import Path

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind


def make_target(store: LocalStore | None = None) -> ReplayTarget:
    rewind = Rewind(
        application="rewind-sources-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
    )

    async def booking() -> None:
        sources = rewind.sources
        candidates = ["seat-a", "seat-b", "seat-c", "seat-d"]
        observations = {
            "time": sources.time(),
            "time_ns": sources.time_ns(),
            "monotonic": sources.monotonic(),
            "monotonic_ns": sources.monotonic_ns(),
            "perf_counter": sources.perf_counter(),
            "perf_counter_ns": sources.perf_counter_ns(),
            "created_at": sources.datetime_now(UTC),
            "day": sources.date_today(),
            "booking_id": sources.uuid4(),
            "random": sources.random(),
            "attempt": sources.randint(1, 10),
            "price": sources.uniform(10.0, 50.0),
            "seat": sources.choice(candidates),
            "alternatives": sources.sample(candidates, 2),
        }
        sources.shuffle(candidates)
        observations["shuffled"] = candidates
        observations["wait"] = await sources.sleep(0.01, "ready")
        # Including observations in the synthetic exception lets replay compare
        # every value, as well as the order and arguments of every source call.
        raise RuntimeError("synthetic booking failed", observations)

    return ReplayTarget(rewind, booking)


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
    return path / f"{created.pop()}.rewind.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/sources-demo"))
    args = parser.parse_args()
    print(asyncio.run(record(args.store)))


if __name__ == "__main__":
    main()
