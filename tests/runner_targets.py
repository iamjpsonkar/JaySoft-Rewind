"""Local factories used to exercise the isolated runner's startup boundary."""

import asyncio
import socket

from examples.http_failure import replay_target


def network_during_import_factory():
    socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    return replay_target()


def swallowed_network_factory():
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    except Exception:
        pass
    return replay_target()


def timeout_factory():
    from rewind import ReplayTarget

    target = replay_target()

    async def hangs(*args, **kwargs):
        await asyncio.sleep(60)

    return ReplayTarget(target.rewind, hangs)


def changed_outcome_factory():
    from rewind import ReplayTarget

    target = replay_target()

    async def fixed(order_id):
        try:
            return await target.entrypoint(order_id)
        except KeyError:
            return "fallback"

    return ReplayTarget(target.rewind, fixed)
