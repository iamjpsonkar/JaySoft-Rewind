"""Command-line artifact inspection, isolated replay, and reproduction tests."""

import argparse
import json
import os
import sys
from pathlib import Path

from .errors import RewindError
from .runner import replay_file
from .storage import LocalStore, load_file
from .version import __version__

EXIT_CODES = {"reproduced": 0, "diverged": 1, "ineligible": 2, "incompatible": 2, "replay_error": 3}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="rewind", description="Inspect and replay recorded Python failures."
    )
    root.add_argument("--version", action="version", version=f"Rewind {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect", help="validate and summarize an artifact without running code"
    )
    inspect.add_argument("artifact", type=Path)
    replay = commands.add_parser("replay", help="replay in a fresh local process")
    replay.add_argument("artifact", type=Path)
    replay.add_argument("--app", required=True, help="local module:function returning ReplayTarget")
    replay.add_argument("--timeout", type=float, default=30)
    replay.add_argument(
        "--isolation", choices=["python-guard", "adapter-only"], default="python-guard"
    )
    listing = commands.add_parser("list", help="list stored recording summaries")
    listing.add_argument("--store", type=Path, default=Path(".rewind/snapshots"))
    delete = commands.add_parser("delete", help="delete exactly one local recording")
    delete.add_argument("snapshot_id")
    delete.add_argument("--store", type=Path, default=Path(".rewind/snapshots"))
    test = commands.add_parser(
        "test", help="generate a pytest reproduction test with the recorded oracle"
    )
    test.add_argument("artifact", type=Path)
    test.add_argument("--app", required=True)
    test.add_argument("--output", type=Path, required=True)
    return root


def summary(path: Path) -> dict:
    data = load_file(path).data
    return {
        "id": data["snapshot_id"],
        "application": data["application"]["name"],
        "created_at": data["created_at"],
        "kind": data["input"]["kind"],
        "complete": data["capture"]["complete"],
        "ineligible_reasons": data["capture"]["ineligible_reasons"],
        "interactions": len(data["interactions"]),
        "outcome": data["outcome"]["kind"],
    }


def generate_test(artifact: Path, app: str, output: Path) -> None:
    snapshot = load_file(artifact)
    if not snapshot.complete:
        raise RewindError("cannot generate a reproduction test from an incomplete capture")
    if output.suffix != ".py":
        raise ValueError("test output must have a .py suffix")
    # Copy the sanitized fixture next to the test for portability. Never overwrite either file.
    fixture = output.with_suffix(".rewind.json")
    if output.exists() or fixture.exists():
        raise FileExistsError("output already exists")
    code = (
        '"""Reproduces the recorded outcome; review assertions when implementing a fix."""\n\n'
        "from pathlib import Path\n\nfrom rewind import replay_file\n\n\n"
        f"def test_reproduction_{snapshot.id}():\n"
        '    artifact = Path(__file__).with_suffix(".rewind.json")\n'
        f"    report = replay_file(artifact, {app!r})\n"
        "    assert report.reproduced, report.to_dict()\n"
    )
    fd = os.open(fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(snapshot.raw)
    try:
        with output.open("x") as stream:
            stream.write(code)
    except Exception:
        fixture.unlink()
        raise


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    result: dict | list
    try:
        if args.command == "inspect":
            result = summary(args.artifact)
        elif args.command == "replay":
            report = replay_file(
                args.artifact, args.app, timeout=args.timeout, isolation=args.isolation
            )
            print(json.dumps(report.to_dict(), ensure_ascii=True, indent=2))
            return EXIT_CODES[report.status]
        elif args.command == "list":
            if not args.store.exists():
                result = []
            else:
                store = LocalStore(args.store)
                result = [summary(store.path / f"{i}.rewind.json") for i in store.ids()]
        elif args.command == "delete":
            LocalStore(args.store).delete(args.snapshot_id)
            result = {"deleted": args.snapshot_id}
        else:
            generate_test(args.artifact, args.app, args.output)
            result = {
                "test": str(args.output),
                "fixture": str(args.output.with_suffix(".rewind.json")),
                "oracle": "recorded outcome",
            }
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (RewindError, OSError, ValueError):
        # Errors must not echo potentially sensitive artifact values or filenames.
        print(
            json.dumps(
                {"error": "operation failed; check artifact, path, permissions, and arguments"}
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
