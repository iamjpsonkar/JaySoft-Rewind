import base64
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from examples.server_demo import make_target, replay_target
from rewind import LocalStore, replay_file
from rewind.codecs import decode


async def request(target, path="/quote"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=target.entrypoint, raise_app_exceptions=False),
        base_url="http://server.example",
    ) as client:
        if path == "/health":
            return await client.get(path)
        return await client.post(path, json={"sku": "demo-widget", "quantity": 2})


def assert_failure_snapshot(snapshot, limits):
    assert snapshot.complete, snapshot.data["capture"]
    assert snapshot.data["diagnostics"]["events"]
    incoming = decode(snapshot.data["input"]["value"], limits)
    assert incoming["scope"]["method"] == "POST"
    assert incoming["scope"]["path"] == "/quote"
    assert json.loads(incoming["body"]) == {"sku": "demo-widget", "quantity": 2}
    interactions = snapshot.data["interactions"]
    assert len(interactions) == 1
    assert interactions[0]["operation"] == "http.request"
    assert interactions[0]["dependency"] == "catalog"
    provider = decode(interactions[0]["outcome"]["value"], limits)
    assert provider["status"] == 200
    assert "unit_price" not in json.loads(base64.b64decode(provider["body"]))
    outcome = decode(snapshot.data["outcome"]["value"], limits)
    assert outcome["status"] == 500
    assert outcome["exception"]["type"] == "builtins.KeyError"
    assert decode(outcome["exception"]["args"], limits) == ("unit_price",)


async def test_server_capture_all_records_success_and_handler_failure(tmp_path):
    store = LocalStore(tmp_path / "snapshots")
    target = make_target(store)
    assert (await request(target, "/health")).status_code == 200
    assert (await request(target)).status_code == 500
    assert len(store.ids()) == 2
    replay = replay_target()
    failures = 0
    for snapshot_id in store.ids():
        snapshot = store.load(snapshot_id)
        outcome = decode(snapshot.data["outcome"]["value"], target.rewind.limits)
        if outcome["status"] == 500:
            assert_failure_snapshot(snapshot, target.rewind.limits)
            failures += 1
        report = await replay.rewind.replay_asgi(snapshot, replay.entrypoint)
        assert report.reproduced, report.to_dict()
    assert failures == 1


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        ("exception", 1),
        ("status >= 500", 1),
        ("status == 200", 1),
        ("duration >= 0ms", 2),
        ("exception or (status >= 500 and duration > 20ms)", 1),
        ("never", 0),
    ],
)
async def test_server_condition_controls_retention_only(tmp_path, when, expected):
    store = LocalStore(tmp_path / "snapshots")
    target = make_target(store, when=when)
    assert (await request(target, "/health")).status_code == 200
    assert (await request(target)).status_code == 500
    assert len(store.ids()) == expected


def test_ordinary_http_to_server_replays_same_handler_in_fresh_worker(tmp_path):
    pytest.importorskip("uvicorn")
    # Ask the OS for a disposable localhost port before starting the server.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    store_path = tmp_path / "snapshots"
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "examples.server_demo",
                "--port",
                str(port),
                "--store",
                str(store_path),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        pytest.fail("demo server did not start")
                    time.sleep(0.05)
            response = httpx.post(
                f"http://127.0.0.1:{port}/quote",
                json={"sku": "demo-widget", "quantity": 2},
                timeout=5,
            )
            assert response.status_code == 500
            store = LocalStore(store_path)
            while not store.ids() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(store.ids()) == 1
            snapshot = store.load(store.ids()[0])
            assert_failure_snapshot(snapshot, replay_target().rewind.limits)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    # The real server is stopped before the worker imports the same route factory.
    artifact = store_path / f"{snapshot.id}.rewind.json"
    report = replay_file(artifact, "examples.server_demo:replay_target")
    assert report.reproduced, report.to_dict()
    assert report.isolation == "python-guard"
    assert report.consumed == 1
    assert Path(artifact).is_file()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rewind",
            "replay",
            str(artifact),
            "--app",
            "examples.server_demo:replay_target",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["status"] == "reproduced"
