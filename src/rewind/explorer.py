"""Bounded, data-only HTML reports with no application imports or external assets."""

import base64
import hashlib
import html
import os
import tempfile
from itertools import islice
from pathlib import Path
from typing import Any

from .codecs import decode
from .limits import Limits
from .snapshot import Snapshot
from .storage import load_file

MAX_REPORT_BYTES = 2 * 1024 * 1024
_SCRIPT = """'use strict';
const entries = [...document.querySelectorAll('.observation')];
const search = document.getElementById('search');
search.addEventListener('input', () => {
  const query = search.value.toLowerCase();
  let visible = 0;
  entries.forEach(entry => {
    entry.hidden = !entry.textContent.toLowerCase().includes(query);
    if (!entry.hidden) visible++;
  });
  document.getElementById('matches').textContent = visible + ' matching observations';
});
document.getElementById('expand').addEventListener('click', () => {
  document.querySelectorAll('details').forEach(item => { if (!item.hidden) item.open = true; });
});
document.getElementById('collapse').addEventListener('click', () => {
  document.querySelectorAll('details').forEach(item => { item.open = false; });
});
"""
_STYLE = """html{color-scheme:light dark}body{font:16px/1.5 system-ui,sans-serif;max-width:1120px;
margin:2rem auto;padding:0 1rem;color:#18263b;background:#f5f7fb}h1,h2{line-height:1.2}
a{color:#174dab}nav, .controls{display:flex;flex-wrap:wrap;gap:1rem;margin:1rem 0}
section{margin:2rem 0}details{background:white;border:1px solid #ccd4df;border-radius:8px;
margin:.7rem 0;padding:1rem}summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 ui-monospace,monospace}
.notice{padding:1rem;border-left:4px solid #916b00;background:#fff4ce}
input,button{font:inherit;padding:.4rem .6rem}label{display:block}small{color:#475569}
[hidden]{display:none!important}
a:focus,button:focus,input:focus,summary:focus{outline:3px solid #4a78bb}
@media(prefers-color-scheme:dark){body{color:#e2e8f0;background:#101826}details{background:#172337;
border-color:#44546a}a{color:#8fbaff}.notice{background:#3b3017}small{color:#b7c5d7}}
"""


def _escape(value: str) -> str:
    return html.escape(value.encode("utf-8", errors="backslashreplace").decode(), quote=True)


def _preview(value: Any, *, characters: int = 12000) -> str:
    parts: list[str] = []
    remaining = characters
    clipped = False

    def emit(text: str) -> None:
        nonlocal remaining, clipped
        if len(text) > remaining:
            clipped = True
        parts.append(text[:remaining])
        remaining = max(0, remaining - len(text))

    def visit(item: Any, depth: int = 0) -> None:
        nonlocal clipped
        if not remaining:
            clipped = True
            return
        if depth > 8:
            emit("[nested preview omitted]")
            return
        if type(item) is bytes:
            prefix = item[:256]
            try:
                display = repr(prefix.decode("utf-8"))
                label = "UTF-8"
            except UnicodeDecodeError:
                display = repr(prefix)
                label = "binary"
            emit(f"bytes ({len(item)} bytes; {label} preview): {display}")
            if len(item) > len(prefix):
                emit(" [remaining bytes omitted]")
        elif type(item) is str:
            emit(repr(item[:512]))
            if len(item) > 512:
                emit(f" [string preview; {len(item)} characters total]")
        elif type(item) in (dict, list, tuple, set, frozenset):
            mapping = type(item) is dict
            opening, closing = ("{", "}") if mapping else ("[", "]")
            if type(item) not in (dict, list):
                emit(type(item).__name__ + " ")
            emit(opening)
            entries = item.items() if mapping else item
            for index, entry in enumerate(islice(entries, 40)):
                if not remaining:
                    clipped = True
                    break
                emit(("," if index else "") + "\n" + "  " * (depth + 1))
                if mapping:
                    key, child = entry
                    visit(key, depth + 1)
                    emit(": ")
                    visit(child, depth + 1)
                else:
                    visit(entry, depth + 1)
            if len(item) > 40:
                emit(f"\n{'  ' * (depth + 1)}[additional items omitted; {len(item)} total]")
            emit("\n" + "  " * depth + closing)
        else:
            # Only fixed data-only codec types reach this formatter.
            emit(repr(item))

    visit(value)
    return "".join(parts) + ("\n[display budget reached]" if clipped else "")


def _pre(value: Any) -> str:
    return "<pre>" + _escape(_preview(value)) + "</pre>"


def _outcome(value: Any, limits: Limits) -> Any:
    if value is None:
        return {"status": "unfinished observation"}
    if value["kind"] == "return":
        return {"returned": decode(value["value"], limits)}
    return {"exception_type": value["type"], "arguments": decode(value["args"], limits)}


def _http_preview(value: Any) -> Any:
    # HTTP adapter bodies are base64 inside otherwise decoded values. Interpret
    # only this documented field on known HTTP observations, never arbitrary text.
    if type(value) is dict and type(value.get("body")) is str:
        try:
            body = base64.b64decode(value["body"], validate=True)
        except ValueError:
            return value
        return {**value, "body": body}
    return value


def render_snapshot(
    snapshot: Snapshot,
    *,
    limits: Limits | None = None,
    max_bytes: int = MAX_REPORT_BYTES,
) -> str:
    """Render a revalidated snapshot, including incomplete captures, as inert HTML."""
    if type(max_bytes) is not int or not 32768 <= max_bytes <= MAX_REPORT_BYTES:
        raise ValueError("HTML report budget must be between 32768 and 2097152 bytes")
    limits = limits or Limits()
    data = Snapshot.from_bytes(snapshot.raw, limits).data
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    style_hash = base64.b64encode(hashlib.sha256(_STYLE.encode()).digest()).decode()
    policy = (
        "default-src 'none'; base-uri 'none'; form-action 'none'; "
        f"script-src 'sha256-{script_hash}'; style-src 'sha256-{style_hash}'"
    )
    header = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{_escape(policy)}">'
        f"<title>Rewind snapshot explorer</title><style>{_STYLE}</style></head><body>"
        "<h1>Rewind snapshot explorer</h1>"
        "<p>Recorded request, dependency observations and outcome. This report does not execute "
        "application code or contact services.</p>"
        '<p class="notice">This report contains captured data and may contain sensitive values. '
        "Keep it private like the source snapshot. Display previews can be shortened; "
        "the snapshot remains unchanged.</p>"
        '<nav aria-label="Snapshot sections"><a href="#overview">Overview</a>'
        '<a href="#request">Request / input</a><a href="#calls">Dependency calls</a>'
        '<a href="#outcome">Final outcome</a><a href="#diagnostics">Diagnostics</a></nav>'
        '<div class="controls"><button id="expand" type="button">Expand all</button>'
        '<button id="collapse" type="button">Collapse all</button></div>'
    )
    footer = f"<script>{_SCRIPT}</script></body></html>"
    parts = [header]
    remaining = max_bytes - len((header + footer).encode()) - 4096
    omitted = 0

    def add(fragment: str) -> None:
        nonlocal remaining, omitted
        size = len(fragment.encode())
        if size > remaining:
            omitted += 1
        else:
            parts.append(fragment)
            remaining -= size

    def detail(title: str, value: Any, *, observation: bool = False, opened: bool = False) -> str:
        attributes = (' class="observation"' if observation else "") + (" open" if opened else "")
        return (
            f"<details{attributes}><summary>{_escape(title[:512])}</summary>{_pre(value)}</details>"
        )

    overview = {
        "snapshot_id": data["snapshot_id"],
        "created_at": data["created_at"],
        "complete": data["capture"]["complete"],
        "ineligible_reasons": data["capture"]["ineligible_reasons"],
        "entrypoint_kind": data["input"]["kind"],
        "application": data["application"],
        "producer": data.get("producer"),
        "capture_policy": data["policy"],
    }
    # Keep navigation targets outside the bounded details so every link resolves.
    parts.append('<section id="overview"><h2>Overview</h2>')
    add(detail("Capture and runtime", overview, opened=True))
    parts.append('</section><section id="request"><h2>Request / input</h2>')
    add(
        detail(
            "Decoded inbound request or callable arguments",
            decode(data["input"]["value"], limits),
            opened=True,
        )
    )
    parts.append('</section><section id="outcome"><h2>Final outcome</h2>')
    add(detail("Recorded final outcome", _outcome(data["outcome"], limits), opened=True))
    parts.append(
        '</section><section id="calls"><h2>Ordered dependency calls</h2>'
        '<label for="search">Filter calls and diagnostic events</label>'
        '<input id="search" type="search" placeholder="Method, dependency or value">'
        '<p id="matches" role="status">All rendered observations shown</p>'
    )
    for item in data["interactions"]:
        request = decode(item["input"], limits)
        outcome = _outcome(item["outcome"], limits)
        if item["operation"] == "http.request":
            request = _http_preview(request)
            if "returned" in outcome:
                outcome["returned"] = _http_preview(outcome["returned"])
        title = f"{item['sequence']}. {item['operation']} — {item['dependency']}"
        add(
            detail(
                title,
                {"arguments": request, "outcome": outcome, "duration_ns": item.get("duration_ns")},
                observation=True,
            )
        )
    if not data["interactions"]:
        parts.append("<p>No dependency calls were recorded.</p>")
    parts.append('</section><section id="diagnostics"><h2>Optional diagnostic timeline</h2>')
    diagnostics = data.get("diagnostics")
    if type(diagnostics) is dict and diagnostics.get("version") == 1:
        add(detail("Diagnostic coverage", {k: v for k, v in diagnostics.items() if k != "events"}))
        for event in diagnostics["events"]:
            title = f"{event['sequence']}. {event['name']} — {event['phase']}"
            add(detail(title, event, observation=True))
    else:
        parts.append("<p>No supported optional function diagnostics were recorded.</p>")
    parts.append(
        "<p>Diagnostics contain only recorded spans and events. They are not a full "
        "execution trace or a reconstructed stack trace.</p></section>"
    )
    if omitted:
        parts.append(
            f'<p class="notice">Report display budget reached: {omitted} detail blocks '
            "omitted. Inspect the source snapshot for the complete recorded data.</p>"
        )
    parts.append(footer)
    report = "".join(parts)
    if len(report.encode()) > max_bytes:
        raise ValueError("HTML report exceeds display byte budget")
    return report


def explore_file(
    source: str | Path,
    output: str | Path,
    limits: Limits | None = None,
) -> Path:
    """Publish a complete private HTML file atomically, never replacing a destination."""
    limits = limits or Limits()
    snapshot = load_file(source, limits)
    content = render_snapshot(snapshot, limits=limits).encode()
    output = Path(output)
    if output.suffix.lower() not in (".html", ".htm"):
        raise ValueError("report output must have an HTML suffix")
    fd, temporary = tempfile.mkstemp(prefix=".rewind-explore-", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        os.unlink(temporary)
    return output
