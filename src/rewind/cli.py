"""Command-line artifact inspection, isolated replay, and reproduction tests."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .codecs import loads
from .comparison import compare_file, generate_comparison_test
from .doctor import diagnose
from .errors import RewindError
from .explorer import explore_file
from .limits import Limits
from .portable import export_snapshot, import_snapshot
from .runner import replay_file
from .snapshot import ID_PATTERN
from .storage import LocalStore, load_file
from .version import __version__

EXIT_CODES = {"reproduced": 0, "diverged": 1, "ineligible": 2, "incompatible": 2, "replay_error": 3}


def _expectations(command: argparse.ArgumentParser) -> None:
    group = command.add_mutually_exclusive_group()
    group.add_argument("--expected-return", help="developer-approved return value as JSON")
    group.add_argument(
        "--expected-outcome", help="developer-approved canonical typed outcome as JSON"
    )


def _expected_options(args: argparse.Namespace) -> dict[str, Any]:
    for field in ("expected_return", "expected_outcome"):
        value = getattr(args, field, None)
        if value is not None:
            decoded = loads(value.encode(), Limits())
            if field == "expected_outcome" and type(decoded) is not dict:
                raise ValueError("expected-outcome must be a canonical outcome object")
            return {field: decoded}
    return {}


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
    inspect.add_argument("--timeline", action="store_true", help="include optional function spans")
    explore = commands.add_parser("explore", help="write a private standalone HTML snapshot report")
    explore.add_argument("artifact", type=Path)
    explore.add_argument("-o", "--output", type=Path, required=True)
    replay = commands.add_parser("replay", help="replay in a fresh local process")
    replay.add_argument("artifact", type=Path)
    replay.add_argument(
        "--app", required=True,
        help="local decorated module:function or module:Class.method, or ReplayTarget factory",
    )
    replay.add_argument("--timeout", type=float, default=30)
    replay.add_argument(
        "--isolation", choices=["python-guard", "adapter-only"], default="python-guard"
    )
    compare = commands.add_parser("compare", help="explicitly compare changed source code")
    compare.add_argument("artifact", type=Path)
    compare.add_argument("--app", required=True)
    compare.add_argument("--timeout", type=float, default=30)
    compare.add_argument(
        "--isolation", choices=["python-guard", "adapter-only"], default="python-guard"
    )
    _expectations(compare)
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
    test.add_argument(
        "--compare-code", action="store_true", help="generate a labeled comparison test"
    )
    _expectations(test)
    export = commands.add_parser("export", help="export a validated portable .rewind archive")
    export.add_argument("artifact", type=Path)
    export.add_argument("-o", "--output", type=Path, required=True)
    importing = commands.add_parser("import", help="validate and import without replacing an ID")
    importing.add_argument("archive", type=Path)
    importing.add_argument("--store", type=Path, default=Path(".rewind/snapshots"))
    commands.add_parser("doctor", help="inspect runtime and installed optional dependencies")
    for command in (inspect, explore, replay, compare, test, export):
        command.add_argument(
            "--store",
            type=Path,
            default=Path(".rewind/snapshots"),
            help="store used when the artifact argument is a snapshot ID",
        )
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
        if hasattr(args, "artifact"):
            identifier = str(args.artifact)
            if ID_PATTERN.fullmatch(identifier) and not args.artifact.exists():
                args.artifact = args.store / f"{identifier}.rewind.json"
        if args.command == "doctor":
            result = diagnose()
        elif args.command == "export":
            output = export_snapshot(args.artifact, args.output)
            result = {"archive": str(output)}
        elif args.command == "import":
            output = import_snapshot(args.archive, LocalStore(args.store))
            result = {"artifact": str(output)}
        elif args.command == "explore":
            output = explore_file(args.artifact, args.output)
            result = {
                "report": str(output), "note": "Report contains captured data; keep it private."
            }
        elif args.command == "inspect":
            result = summary(args.artifact)
            if args.timeline:
                data = load_file(args.artifact).data
                diagnostic = data.get("diagnostics", {})
                result["diagnostics"] = diagnostic if diagnostic.get("version") == 1 else None
                result["dependencies"] = [
                    {
                        key: item.get(key)
                        for key in ("sequence", "operation", "dependency", "duration_ns")
                    }
                    for item in data["interactions"]
                ]
        elif args.command == "compare":
            comparison = compare_file(
                args.artifact,
                args.app,
                timeout=args.timeout,
                isolation=args.isolation,
                **_expected_options(args),
            )
            print(json.dumps(comparison.to_dict(), ensure_ascii=True, indent=2))
            return 0 if comparison.matched else EXIT_CODES.get(comparison.status, 3)
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
            expectations = _expected_options(args)
            if args.compare_code:
                generate_comparison_test(args.artifact, args.app, args.output, **expectations)
            else:
                if expectations:
                    raise ValueError("expected outcomes require --compare-code")
                generate_test(args.artifact, args.app, args.output)
            result = {
                "test": str(args.output),
                "fixture": str(args.output.with_suffix(".rewind.json")),
                "oracle": "developer outcome" if expectations else "recorded outcome",
                "mode": "comparison" if args.compare_code else "strict",
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
