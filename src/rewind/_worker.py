"""Private replay process. Artifact strings never determine application imports."""

import asyncio
import contextlib
import importlib
import json
import os
import sys
from dataclasses import replace
from typing import Any

from .comparison import (
    _UNSET,
    ComparisonReport,
    _expectation,
    comparison_report,
    prepare_comparison,
)
from .limits import Limits
from .replay import ReplayReport
from .runner import NetworkGuard, ReplayTarget
from .storage import load_file


def execute(
    path: str,
    factory_name: str,
    isolation: str,
    comparison: dict[str, Any] | None = None,
) -> ReplayReport | ComparisonReport:
    guard = NetworkGuard()
    if isolation == "python-guard":
        guard.install()
    comparison_state = ComparisonReport(
        "replay_error",
        "comparison failed",
        isolation=isolation,
        expected_source=(
            "developer"
            if type(comparison) is dict and comparison.get("expected_outcome") is not None
            else "recorded"
        ),
    )

    def finish(report: ReplayReport) -> ReplayReport | ComparisonReport:
        report = replace(report, isolation=isolation)
        return comparison_report(report, comparison_state) if comparison is not None else report

    try:
        snapshot = load_file(path)
        if not snapshot.complete:
            return finish(
                ReplayReport(
                    "ineligible",
                    "capture is incomplete; inspect eligibility reasons",
                )
            )
        if comparison is not None:
            if type(comparison) is not dict or set(comparison) != {"expected_outcome"}:
                raise ValueError("invalid comparison options")
            expectation = _expectation(_UNSET, comparison["expected_outcome"])
            comparison_state = replace(
                comparison_state,
                expected_source=("developer" if expectation is not None else "recorded"),
            )
        module, separator, function = factory_name.partition(":")
        if not separator or not module or not function.isidentifier():
            return finish(ReplayReport("replay_error", "factory must use module:function syntax"))
        target = getattr(importlib.import_module(module), function)()
        if not isinstance(target, ReplayTarget):
            return finish(ReplayReport("replay_error", "factory must return ReplayTarget"))
        if guard.violations:
            return finish(
                ReplayReport("replay_error", "replay attempted a blocked external operation")
            )
        if comparison is not None:
            prepared, comparison_state = prepare_comparison(snapshot, target, comparison, isolation)
            if prepared is None:
                return comparison_state
            snapshot = prepared
        if target.kind == "callable":
            report = asyncio.run(target.rewind.replay(snapshot, target.entrypoint))
        elif target.kind == "asgi":
            report = asyncio.run(target.rewind.replay_asgi(snapshot, target.entrypoint))
        elif target.kind == "callable_sync":
            report = target.rewind.replay_sync(snapshot, target.entrypoint)
        elif target.kind == "wsgi":
            report = target.rewind.replay_wsgi(snapshot, target.entrypoint)
        else:
            return finish(ReplayReport("incompatible", "unsupported local entry point kind"))
        if guard.violations:
            return finish(
                ReplayReport("replay_error", "replay attempted a blocked external operation")
            )
        return finish(report)
    except Exception:
        detail = (
            "replay attempted a blocked external operation"
            if guard.violations
            else "artifact or local factory could not be loaded; check configuration"
        )
        return finish(ReplayReport("replay_error", detail))


def main() -> int:
    if len(sys.argv) not in (4, 5) or (len(sys.argv) == 5 and sys.argv[4] != "compare"):
        return 2
    comparison = None
    if len(sys.argv) == 5:
        from .codecs import loads

        try:
            comparison = loads(sys.stdin.buffer.read(Limits().snapshot_bytes + 1), Limits())
            if type(comparison) is not dict:
                raise ValueError("invalid comparison input")
        except Exception:
            print(
                json.dumps(ComparisonReport("replay_error", "invalid comparison options").to_dict())
            )
            return 0
    # Keep application prints and tracebacks out of reports and fixture data.
    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        report = execute(sys.argv[1], sys.argv[2], sys.argv[3], comparison=comparison)
    print(json.dumps(report.to_dict(), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
