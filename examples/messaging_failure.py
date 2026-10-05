"""Capture an eager Celery result and replay without creating a Celery application."""

import argparse
from pathlib import Path

from celery import Celery

from rewind import CapturePolicy, LocalStore, ReplayTarget, Rewind
from rewind.adapters.celery import RecordingCelery


def make_target(store: LocalStore | None = None, app: Celery | None = None) -> ReplayTarget:
    rewind = Rewind(
        application="messaging-failure", code_paths=[__file__], store=store,
        policy=CapturePolicy.synthetic(),
    )
    tasks = RecordingCelery(app, dependency="orders")

    def operation() -> None:
        result = tasks.task("example.confirm").delay(7)
        payload = result.get(timeout=1)
        assert result.status == "SUCCESS"
        payload["confirmation_id"]

    return ReplayTarget(rewind, operation, kind="callable_sync")


def replay_target() -> ReplayTarget:
    return make_target()


def record(path: Path) -> Path:
    app = Celery("rewind-messaging-demo", broker="memory://", backend="cache+memory://")
    app.conf.task_always_eager = True

    @app.task(name="example.confirm", shared=False)
    def confirm(order_id: int) -> dict[str, int | str]:
        return {"order_id": order_id, "state": "pending"}

    store = LocalStore(path)
    before = set(store.ids())
    target = make_target(store, app)
    try:
        target.rewind.run_sync(target.entrypoint)
    except KeyError:
        pass
    finally:
        app.close()
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("messaging demo did not persist one snapshot")
    identifier = created.pop()
    if not store.load(identifier).complete:
        raise RuntimeError("messaging demo is incomplete")
    return path / f"{identifier}.rewind.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".rewind/messaging-demo"))
    print(record(parser.parse_args().store))
