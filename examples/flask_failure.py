"""Capture a synthetic Flask failure and reproduce its clock/UUID observations."""

import argparse
import json
from pathlib import Path

from flask import Flask, request
from werkzeug.test import EnvironBuilder

from rewind import CapturePolicy, LocalStore, Retention, Rewind
from rewind.runner import ReplayTarget


def build(store: LocalStore | None = None) -> tuple[Rewind, Flask]:
    rewind = Rewind(
        application="flask-checkout-demo",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
        retain=Retention(status_at_least=500),
    )
    app = Flask(__name__)

    @app.post("/checkout")
    def checkout():
        order = request.get_json()
        return {
            "order_id": order["order_id"],
            "status": "gateway-unavailable",
            "request_id": rewind.sources.uuid4(),
            "observed_at": rewind.sources.time(),
        }, 503

    return rewind, app


def replay_target() -> ReplayTarget:
    rewind, app = build()
    return ReplayTarget(rewind, app, kind="wsgi")


def record(path: Path) -> Path:
    store = LocalStore(path)
    before = set(store.ids())
    rewind, app = build(store)
    incoming = EnvironBuilder(
        method="POST", path="/checkout", json={"order_id": "synthetic-order"}
    ).get_environ()
    response = rewind.wsgi(app)(incoming, lambda *args: lambda body: None)
    try:
        for _ in response:
            pass
    finally:
        response.close()
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("Flask demo did not produce one snapshot")
    identifier = created.pop()
    if not store.load(identifier).complete:
        raise RuntimeError("Flask demo recording is incomplete")
    return path / f"{identifier}.rewind.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=".rewind/flask-demo")
    args = parser.parse_args()
    artifact = record(Path(args.store))
    target = replay_target()
    snapshot = LocalStore(args.store).load(artifact.name.removesuffix(".rewind.json"))
    print(
        json.dumps(
            {
                "snapshot": str(artifact),
                "report": target.rewind.replay_wsgi(snapshot, target.entrypoint).to_dict(),
            }
        )
    )


if __name__ == "__main__":
    main()
