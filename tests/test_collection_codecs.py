import pytest

from rewind import CapturePolicy, Limits
from rewind.codecs import decode, encode
from rewind.errors import InvalidSnapshot


@pytest.mark.parametrize(
    "value",
    [
        {b"key": b"value", 7: "number", ("tuple", 1): None},
        {b"a", b"b"},
        frozenset((1, 2)),
        {"nested": {b"field": {1, 2}}},
    ],
)
def test_mapping_and_set_types_roundtrip(value):
    restored = decode(encode(value, Limits()), Limits())
    assert type(restored) is type(value)
    assert restored == value


def test_sets_have_canonical_order_for_strict_matching():
    assert encode({"b", "a"}, Limits()) == encode({"a", "b"}, Limits())


@pytest.mark.parametrize("tag", ["set", "frozenset", "mapping"])
def test_unhashable_decoded_data_rejected(tag):
    value = encode([], Limits())
    node = {"t": tag, "v": [[value, encode(1, Limits())]] if tag == "mapping" else [value]}
    with pytest.raises(InvalidSnapshot):
        decode(node, Limits())


def test_duplicate_mapping_keys_and_set_values_rejected():
    scalar = encode(1, Limits())
    with pytest.raises(InvalidSnapshot):
        decode({"t": "mapping", "v": [[scalar, scalar], [scalar, scalar]]}, Limits())
    with pytest.raises(InvalidSnapshot):
        decode({"t": "set", "v": [scalar, scalar]}, Limits())


def test_byte_keyed_secrets_are_redacted():
    reasons = []
    packed = CapturePolicy.synthetic().value({b"api_key": b"private"}, Limits(), reasons.append)
    assert decode(packed, Limits()) == {b"api_key": "[REDACTED]"}
    assert reasons == ["sensitive_value_removed"]


async def test_callable_can_return_redis_style_mapping_and_set(recorder):
    async def operation():
        return {b"fields": {b"one", b"two"}}

    await recorder.run(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.complete
    assert (await recorder.replay(snapshot, operation)).reproduced
