"""Record a local backend's JSON response and replay it without calling the server."""

import argparse
import json
from pathlib import Path

import httpx

from rewind import CapturePolicy, LocalStore, ReplayTarget, Retention, Rewind


def make_target(
    store: LocalStore | None = None,
    headers: dict[str, str] | None = None,
    *,
    live: bool = False,
) -> ReplayTarget:
    rewind = Rewind(
        application="existing-backend-response",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
        retain=Retention(always=True),
    )

    def operation(url: str) -> dict:
        def fetch_response() -> dict:
            if not live:
                raise AssertionError("replay attempted to call the backend")
            with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
                response = client.get(url, headers=headers)
                return {"status": response.status_code, "body": response.json()}

        # This explicit boundary records the returned value. Authentication and
        # the remote server's internals are outside this recording boundary.
        return rewind.value("backend.json_response", fetch_response)

    return ReplayTarget(rewind, operation, kind="callable_sync")


def replay_target() -> ReplayTarget:
    # No headers file or live client is required to replay the recorded value.
    return make_target()


def record(url: str, path: Path, headers: dict[str, str] | None = None) -> dict:
    store = LocalStore(path)
    target = make_target(store, headers, live=True)
    before = set(store.ids())
    result = target.rewind.run_sync(target.entrypoint, url)
    created = set(store.ids()) - before
    if len(created) != 1:
        raise RuntimeError("expected exactly one retained recording")
    snapshot = store.load(created.pop())
    return {
        "http_status": result["status"],
        "snapshot": str(path / f"{snapshot.id}.rewind.json"),
        "complete": snapshot.complete,
        "ineligible_reasons": snapshot.data["capture"]["ineligible_reasons"],
        "boundary": "returned HTTP status and JSON; no server internals",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="local/test endpoint returning JSON")
    parser.add_argument("--headers-file", type=Path, help="private JSON object of request headers")
    parser.add_argument("--store", type=Path, default=Path(".rewind/backend-demo"))
    args = parser.parse_args()
    headers = None
    if args.headers_file is not None:
        headers = json.loads(args.headers_file.read_text())
        if type(headers) is not dict or any(
            type(k) is not str or type(v) is not str for k, v in headers.items()
        ):
            parser.error("headers file must contain a JSON object of string header values")
    print(json.dumps(record(args.url, args.store, headers), indent=2))


if __name__ == "__main__":
    main()
