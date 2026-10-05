"""Explicit changed-source comparison, separate from strict reproduction."""

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .codecs import decode, encode
from .limits import Limits
from .policy import CapturePolicy
from .replay import ReplayReport
from .snapshot import Snapshot
from .storage import load_file

_UNSET = object()


@dataclass(frozen=True)
class ComparisonReport:
    status: str
    detail: str
    consumed: int = 0
    total: int = 0
    isolation: str = "python-guard"
    code_changed: bool | None = None
    expected_source: str = "recorded"
    mode: str = "comparison"

    @property
    def matched(self) -> bool:
        return self.status == "matched"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _expectation(expected_return: Any, expected_outcome: Any) -> dict[str, Any] | None:
    if expected_return is not _UNSET and expected_outcome is not None:
        raise ValueError("provide expected_return or expected_outcome, not both")
    if expected_return is not _UNSET:
        return {"kind": "return", "value": encode(expected_return, Limits())}
    if expected_outcome is not None:
        if type(expected_outcome) is not dict:
            raise ValueError("expected_outcome must be a canonical outcome dictionary")
        if expected_outcome.get("kind") == "return" and set(expected_outcome) == {"kind", "value"}:
            value = decode(expected_outcome["value"], Limits())
            return {"kind": "return", "value": encode(value, Limits())}
        if (
            expected_outcome.get("kind") == "exception"
            and set(expected_outcome) == {"kind", "type", "args"}
            and type(expected_outcome["type"]) is str
        ):
            args = decode(expected_outcome["args"], Limits())
            if type(args) is not tuple:
                raise ValueError("expected exception arguments must encode a tuple")
            return {
                "kind": "exception",
                "type": expected_outcome["type"],
                "args": encode(args, Limits()),
            }
        raise ValueError("invalid canonical expected outcome")
    return None


def compare_file(
    path: str | Path,
    factory: str,
    *,
    expected_return: Any = _UNSET,
    expected_outcome: dict[str, Any] | None = None,
    timeout: float = 30,
    isolation: str = "python-guard",
) -> ComparisonReport:
    """Compare local code with recorded observations and an explicit outcome oracle.

    Only the declared source digest may differ. No desired outcome is inferred:
    without an expectation the comparison checks the original recorded outcome.
    """
    from .runner import _run_worker

    expectation = _expectation(expected_return, expected_outcome)
    data = _run_worker(
        path,
        factory,
        timeout=timeout,
        isolation=isolation,
        comparison={"expected_outcome": expectation},
    )
    data.setdefault("expected_source", "developer" if expectation is not None else "recorded")
    try:
        report = ComparisonReport(**data)
        if report.status == "reproduced" or report.mode != "comparison":
            raise ValueError("not a comparison report")
        return report
    except (TypeError, ValueError):
        return ComparisonReport(
            "replay_error", "invalid comparison worker report", isolation=isolation
        )


def prepare_comparison(
    snapshot: Snapshot,
    target: Any,
    options: dict[str, Any],
    isolation: str,
) -> tuple[Snapshot | None, ComparisonReport]:
    """Validate invariants and make an isolated in-memory comparison snapshot."""
    if type(options) is not dict or set(options) != {"expected_outcome"}:
        raise ValueError("invalid comparison options")
    expectation = _expectation(_UNSET, options["expected_outcome"])
    data = snapshot.data
    expected_source = "developer" if expectation is not None else "recorded"
    changed = data["application"]["code_digest"] != target.rewind.application["code_digest"]
    report = ComparisonReport(
        "incompatible",
        "comparison compatibility check failed",
        isolation=isolation,
        code_changed=changed,
        expected_source=expected_source,
    )
    recorded_application = dict(data["application"])
    recorded_application["code_digest"] = target.rewind.application["code_digest"]
    if recorded_application != target.rewind.application:
        return None, report
    # Normalize old policy documents through the same public policy constructor.
    if CapturePolicy(**data["policy"]).to_dict() != target.rewind.policy.to_dict():
        return None, report
    if data["input"]["kind"] != target.kind:
        return None, report
    data["application"] = recorded_application
    if expectation is not None:
        data["outcome"] = expectation
    return Snapshot.from_dict(data, target.rewind.limits), report


def comparison_report(report: ReplayReport, comparison: ComparisonReport) -> ComparisonReport:
    status = "matched" if report.reproduced else report.status
    detail = (
        "recorded observations and expected outcome matched" if report.reproduced else report.detail
    )
    return ComparisonReport(
        status,
        detail,
        report.consumed,
        report.total,
        report.isolation,
        comparison.code_changed,
        comparison.expected_source,
    )


def generate_comparison_test(
    artifact: str | Path,
    factory: str,
    output: str | Path,
    *,
    expected_return: Any = _UNSET,
    expected_outcome: dict[str, Any] | None = None,
) -> None:
    """Write a deterministic comparison test and an unchanged artifact copy.

    The oracle is explicit in the generated test. Omitting it checks the recorded
    outcome, which can be an exception; it does not claim the application is fixed.
    """
    expectation = _expectation(expected_return, expected_outcome)
    snapshot = load_file(Path(artifact))
    if not snapshot.complete:
        raise ValueError("incomplete recordings cannot generate comparison tests")
    destination = Path(output)
    if destination.suffix != ".py":
        raise ValueError("test output must have a .py suffix")
    fixture = destination.with_suffix(".rewind.json")
    if destination == fixture or destination.exists() or fixture.exists():
        raise FileExistsError("output already exists or overlaps fixture")
    source = "developer" if expectation is not None else "recorded"
    code = (
        f'"""Changed-code comparison against the {source} outcome; not strict reproduction."""\n\n'
        "from pathlib import Path\n\nfrom rewind.comparison import compare_file\n\n\n"
        f"def test_comparison_{snapshot.id}():\n"
        '    artifact = Path(__file__).with_suffix(".rewind.json")\n'
        f"    report = compare_file(artifact, {factory!r}, expected_outcome={expectation!r})\n"
        "    assert report.matched, report.to_dict()\n"
    )
    fd = os.open(fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(snapshot.raw)
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(code)
    except BaseException:
        fixture.unlink()
        raise
