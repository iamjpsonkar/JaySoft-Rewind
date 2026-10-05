"""Capture a synthetic Flask failure and reproduce its clock/UUID observations."""

import argparse
import json

from flask import Flask, request
from werkzeug.test import EnvironBuilder

from rewind import CapturePolicy, LocalStore, Retention, Rewind
from rewind.runner import ReplayTarget


def build(store: LocalStore | None = None) -> tuple[Rewind, Flask]:
    rewind = Rewind(
        application="flask-checkout-demo", code_paths=[__file__], store=store,
        policy=CapturePolicy.synthetic(), retain=Retention(status_at_least=500),
    )
    app = Flask(__name__)

    @app.post("/checkout")
    def checkout():
        order = request.get_json()
        return {
            "order_id": order["order_id"], "status": "gateway-unavailable",
            "request_id": rewind.sources.uuid4(), "observed_at": rewind.sources.time(),
        }, 503

    return rewind, app


def replay_target() -> ReplayTarget:
    rewind, app = build()
    return ReplayTarget(rewind, app, kind="wsgi")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=".rewind/flask-demo")
    args = parser.parse_args()
    store = LocalStore(args.store)
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
    snapshot = store.load(store.ids()[-1])
    print(json.dumps({
        "snapshot": str(store.path / f"{snapshot.id}.rewind.json"),
        "report": rewind.replay_wsgi(snapshot, app).to_dict(),
    }))


if __name__ == "__main__":
    main()
