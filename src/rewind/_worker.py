"""Private replay process. Artifact strings never determine application imports."""

import asyncio
import contextlib
import importlib
import json
import os
import sys
from dataclasses import replace

from .replay import ReplayReport
from .runner import NetworkGuard, ReplayTarget
from .storage import load_file


def execute(path: str, factory_name: str, isolation: str) -> ReplayReport:
    guard = NetworkGuard()
    if isolation == "python-guard":
        guard.install()
    try:
        snapshot = load_file(path)
        if not snapshot.complete:
            return ReplayReport(
                "ineligible",
                "capture is incomplete; inspect eligibility reasons",
                isolation=isolation,
            )
        module, separator, function = factory_name.partition(":")
        if not separator or not module or not function.isidentifier():
            return ReplayReport(
                "replay_error", "factory must use module:function syntax", isolation=isolation
            )
        target = getattr(importlib.import_module(module), function)()
        if not isinstance(target, ReplayTarget):
            return ReplayReport(
                "replay_error", "factory must return ReplayTarget", isolation=isolation
            )
        if target.kind == "callable":
            report = asyncio.run(target.rewind.replay(snapshot, target.entrypoint))
        elif target.kind == "asgi":
            report = asyncio.run(target.rewind.replay_asgi(snapshot, target.entrypoint))
        else:
            return ReplayReport(
                "incompatible", "unsupported local entry point kind", isolation=isolation
            )
        if guard.violations:
            return ReplayReport(
                "replay_error", "replay attempted a blocked external operation", isolation=isolation
            )
        return replace(report, isolation=isolation)
    except Exception:
        detail = (
            "replay attempted a blocked external operation"
            if guard.violations
            else "artifact or local factory could not be loaded; check configuration"
        )
        return ReplayReport("replay_error", detail, isolation=isolation)


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    # Keep application prints and tracebacks out of reports and fixture data.
    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        report = execute(*sys.argv[1:])
    print(json.dumps(report.to_dict(), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
