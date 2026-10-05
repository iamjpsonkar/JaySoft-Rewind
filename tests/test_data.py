import json
from pathlib import Path

import pytest

from rewind.codecs import decode, encode, loads
from rewind.errors import CaptureLimit, InvalidSnapshot
from rewind.limits import Limits
from rewind.policy import CapturePolicy


def test_codec_detaches_and_preserves_builtin_types():
    original = {"x": ([b"binary", 42, None, True], {"t": "bytes", "v": "user data"})}
    packed = encode(original, Limits())
    original["x"][0].append("later mutation")
    assert decode(packed, Limits()) == {
        "x": ([b"binary", 42, None, True], {"t": "bytes", "v": "user data"})
    }


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), object(), {object(): "key"}, Path("/tmp")]
)
def test_unsupported_values_are_rejected(value):
    with pytest.raises(CaptureLimit):
        encode(value, Limits())


def test_cyclic_values_and_large_payloads_are_bounded():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(CaptureLimit):
        encode(cycle, Limits())
    with pytest.raises(CaptureLimit):
        encode("a" * 1024 * 1024, Limits())


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b"NaN", b"{bad", b'"\xff"'])
def test_json_loader_rejects_ambiguous_or_invalid_input(raw):
    with pytest.raises(InvalidSnapshot):
        loads(raw, Limits())


def test_redaction_covers_nested_values_url_and_headers():
    policy = CapturePolicy.synthetic()
    reasons = []
    value = policy.value({"a": [{"access_token": "never-save"}], "ok": 3}, Limits(), reasons.append)
    headers = policy.headers([(b"Authorization", b"never-save")], reasons.append)
    url = policy.url("https://user:never-save@example.com/?api_key=never-save&x=1", reasons.append)
    assert "never-save" not in json.dumps([value, headers, url])
    assert reasons
    assert decode(value, Limits())["ok"] == 3


def test_default_policy_excludes_values_and_bodies():
    reasons = []
    policy = CapturePolicy()
    assert decode(policy.value({"customer": "private"}, Limits(), reasons.append), Limits()) is None
    assert policy.body(b"customer data", "text/plain", Limits(), reasons.append) == ""
    assert set(reasons) == {"values_excluded", "body_excluded"}
