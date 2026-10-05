import base64
import hashlib
import importlib
import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from rewind import CapturePolicy
from rewind.cli import main
from rewind.codecs import encode
from rewind.errors import InvalidSnapshot
from rewind.explorer import _SCRIPT, explore_file, render_snapshot
from rewind.limits import Limits
from rewind.snapshot import Snapshot
from rewind.storage import load_file


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.attributes = []
        self.ids = set()
        self.hrefs = []
        self.content = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if "href" in values:
            self.hrefs.append(values["href"])

    def handle_data(self, data):
        self.content.append(data)


def document(**changes):
    data = load_file(Path(__file__).parent / "fixtures/golden-v0.1.json").data
    data.update(changes)
    return Snapshot.from_dict(data)


def test_readable_request_dependencies_outcome_and_runtime():
    snapshot = document()
    report = render_snapshot(snapshot)
    parsed = Document(report)
    text = "".join(parsed.content)
    for label in (
        "golden-contract",
        "0.1.0a2",
        "python",
        "Decoded inbound request",
        "Ordered dependency calls",
        "Recorded final outcome",
        "bytes (",
        "input",
    ):
        assert label in text
    assert all(href.startswith("#") and href[1:] in parsed.ids for href in parsed.hrefs)
    assert not any(name in ("src", "action") for name, value in parsed.attributes)
    assert parsed.tags.count("script") == 1
    digest = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    assert f"sha256-{digest}" in report


async def test_real_asgi_request_dependency_and_response_are_readable(tmp_path):
    from examples.fastapi_failure import record

    source = await record(tmp_path / "recordings")
    output = explore_file(source, tmp_path / "report.html")
    text = "".join(Document(output.read_text()).content)
    for value in ("/checkout", "POST", "order-demo", "gateway", "503", "502"):
        assert value in text


def test_all_payload_html_is_inert_in_text_nodes():
    payload = "</script><script>alert(1)</script><img src=x onerror=alert(2)><svg onload=alert(3)>"
    data = document().data
    data["application"]["name"] = payload
    data["interactions"][0]["dependency"] = payload
    data["input"]["value"] = encode({"args": (payload, payload.encode()), "kwargs": {}}, Limits())
    data["outcome"] = {"kind": "exception", "type": payload, "args": encode((payload,), Limits())}
    report = render_snapshot(Snapshot.from_dict(data))
    parsed = Document(report)
    assert payload in "".join(parsed.content)
    assert "img" not in parsed.tags and "svg" not in parsed.tags
    assert parsed.tags.count("script") == 1
    assert not any(name.startswith("on") for name, value in parsed.attributes)
    assert "&lt;/script&gt;" in report
    assert payload not in report


def test_incomplete_capture_and_unfinished_observation_are_readable():
    data = document().data
    data["capture"] = {"complete": False, "ineligible_reasons": ["body_excluded"]}
    data["interactions"][0]["outcome"] = None
    report = render_snapshot(Snapshot.from_dict(data))
    text = "".join(Document(report).content)
    assert "False" in text and "body_excluded" in text and "unfinished observation" in text


def test_binary_preview_and_entire_report_are_bounded():
    data = document().data
    data["outcome"] = {"kind": "return", "value": encode(b"A" * 100_000 + b"ENDMARKER", Limits())}
    item = data["interactions"][0]
    data["interactions"] = [{**item, "sequence": i + 1} for i in range(200)]
    report = render_snapshot(Snapshot.from_dict(data), max_bytes=32768)
    assert len(report.encode()) <= 32768
    assert "ENDMARKER" not in report
    assert "100009 bytes" in report and "remaining bytes omitted" in report
    assert "detail blocks omitted" in report
    parsed = Document(report)
    assert all(href[1:] in parsed.ids for href in parsed.hrefs)
    assert report.endswith("</body></html>")


def test_known_http_body_field_is_decoded():
    data = document().data
    data["interactions"] = [
        {
            "sequence": 1,
            "operation": "http.request",
            "dependency": "httpx",
            "input": encode(
                {"method": "POST", "body": base64.b64encode(b'{"id":7}').decode()}, Limits()
            ),
            "outcome": {
                "kind": "return",
                "value": encode({"body": base64.b64encode(b"pending").decode()}, Limits()),
            },
        }
    ]
    text = "".join(Document(render_snapshot(Snapshot.from_dict(data))).content)
    assert "UTF-8 preview" in text and '{"id":7}' in text and "pending" in text


def test_diagnostics_include_dropped_count_and_real_fields():
    data = document().data
    data["diagnostics"] = {
        "version": 1,
        "dropped": 3,
        "events": [
            {
                "sequence": 1,
                "span_id": 1,
                "parent_id": None,
                "kind": "function",
                "phase": "enter",
                "name": "fixture.handler",
                "offset_ns": 3,
                "elapsed_ns": None,
                "exception_type": None,
            }
        ],
    }
    text = "".join(Document(render_snapshot(Snapshot.from_dict(data))).content)
    assert "fixture.handler" in text and "'dropped': 3" in text and "'offset_ns': 3" in text
    assert "not a full execution trace" in text


def test_revalidates_even_direct_snapshot_constructor_before_output(tmp_path):
    with pytest.raises(InvalidSnapshot):
        render_snapshot(Snapshot(b'{"module":"payload"}'))
    source = tmp_path / "invalid.json"
    source.write_text('{"module":"payload"}')
    output = tmp_path / "report.html"
    with pytest.raises(InvalidSnapshot):
        explore_file(source, output)
    assert not output.exists()


def test_report_does_not_import_code_or_open_network(tmp_path, monkeypatch):
    source = tmp_path / "snapshot.json"
    source.write_bytes(document().raw)

    def forbidden(*args, **kwargs):
        pytest.fail("application import or external connection during exploration")

    monkeypatch.setattr(importlib, "import_module", forbidden)
    import socket

    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert explore_file(source, tmp_path / "report.html").is_file()


def test_cli_writes_private_atomic_report_without_clobber(recorder, tmp_path, capsys):
    recorder.policy = CapturePolicy.synthetic()
    recorder.run_sync(lambda: "synthetic")
    identifier = recorder.store.ids()[0]
    output = tmp_path / "report.html"
    command = ["explore", identifier, "--store", str(recorder.store.path), "--output", str(output)]
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["report"] == str(output) and "private" in result["note"]
    assert output.stat().st_mode & 0o777 == 0o600
    original = output.read_bytes()
    assert main(command) == 3
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".rewind-explore-*"))


def test_destination_symlink_is_not_followed(tmp_path):
    source = tmp_path / "snapshot.json"
    source.write_bytes(document().raw)
    existing = tmp_path / "existing"
    existing.write_text("untouched")
    output = tmp_path / "report.html"
    output.symlink_to(existing)
    with pytest.raises(FileExistsError):
        explore_file(source, output)
    assert existing.read_text() == "untouched"
    assert not list(tmp_path.glob(".rewind-explore-*"))


def test_atomic_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    source = tmp_path / "snapshot.json"
    source.write_bytes(document().raw)
    output = tmp_path / "report.html"

    def broken_link(*args):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(os, "link", broken_link)
    with pytest.raises(OSError):
        explore_file(source, output)
    assert not output.exists() and not list(tmp_path.glob(".rewind-explore-*"))


def test_local_search_and_expansion_script():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is optional for the local controls smoke check")
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const entries = [{textContent: 'HTTP alpha', hidden:false, open:false},
                 {textContent: 'Redis beta', hidden:false, open:false}];
const nodes = Object.fromEntries(['search','matches','expand','collapse'].map(name =>
  [name, {value:'', textContent:'', addEventListener(event, callback){this[event]=callback;}}]));
const document = {querySelectorAll(){return entries;}, getElementById(id){return nodes[id];}};
vm.runInNewContext(SCRIPT, {document});
nodes.search.value = 'redis'; nodes.search.input();
assert.equal(entries[0].hidden, true); assert.equal(entries[1].hidden, false);
assert.equal(nodes.matches.textContent, '1 matching observations');
nodes.expand.click(); assert.equal(entries[1].open,true); assert.equal(entries[0].open,false);
nodes.collapse.click(); assert.equal(entries[1].open,false);
""".replace("SCRIPT", json.dumps(_SCRIPT))
    subprocess.run([node, "-e", harness], check=True, timeout=10, capture_output=True)
