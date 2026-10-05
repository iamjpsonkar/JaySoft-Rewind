"""Exercise fresh-process replay inside the network-disabled Docker profile."""

import asyncio
import json
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

from examples import (
    background_capture,
    combined_failure,
    database_failure,
    fastapi_failure,
    flask_failure,
    http_failure,
    messaging_failure,
    sources_failure,
)
from rewind import replay_file


def main() -> None:
    # This probe runs outside Rewind's child audit guard.
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=1):
            pass
    except OSError:
        pass
    else:
        raise RuntimeError("external network was reachable; use --network none")

    with TemporaryDirectory(prefix="rewind-offline-") as directory:
        for name, example in (
            ("http", http_failure),
            ("fastapi", fastapi_failure),
            ("sources", sources_failure),
            ("database", database_failure),
            ("combined", combined_failure),
        ):
            artifact = asyncio.run(example.record(Path(directory) / name))
            report = replay_file(artifact, f"examples.{name}_failure:replay_target")
            if not report.reproduced or report.consumed != report.total:
                raise RuntimeError(f"{name} replay failed: {report.to_dict()}")
            print(json.dumps({"example": name, **report.to_dict()}))

        messaging_artifact = messaging_failure.record(Path(directory) / "messaging")
        messaging_report = replay_file(
            messaging_artifact, "examples.messaging_failure:replay_target"
        )
        if not messaging_report.reproduced:
            raise RuntimeError(f"messaging replay failed: {messaging_report.to_dict()}")
        print(json.dumps({"example": "messaging", **messaging_report.to_dict()}))

        flask_artifact = flask_failure.record(Path(directory) / "flask")
        flask_report = replay_file(flask_artifact, "examples.flask_failure:replay_target")
        if not flask_report.reproduced or flask_report.consumed != flask_report.total:
            raise RuntimeError(f"Flask replay failed: {flask_report.to_dict()}")
        print(json.dumps({"example": "flask", **flask_report.to_dict()}))

        artifact, stats = asyncio.run(background_capture.record(Path(directory) / "background"))
        report = replay_file(artifact, "examples.background_capture:replay_target")
        if (
            not report.reproduced
            or report.consumed != report.total
            or stats["persisted"] != 1
            or stats["pending_items"] != 0
            or stats["persistence_failed"] != 0
        ):
            raise RuntimeError("background capture did not drain and replay successfully")
        print(json.dumps({"example": "background", **report.to_dict()}))


if __name__ == "__main__":
    main()
