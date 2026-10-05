from decimal import Decimal

import pytest

from rewind.codecs import decode, encode
from rewind.errors import CaptureLimit, InvalidSnapshot
from rewind.limits import Limits


@pytest.mark.parametrize("text", ["0", "-0.00", "123.4500", "1E+100", "-1E-100"])
def test_decimal_preserves_exact_value_and_scale(text):
    number = Decimal(text)
    restored = decode(encode(number, Limits()), Limits())
    assert type(restored) is Decimal
    assert restored.as_tuple() == number.as_tuple()


@pytest.mark.parametrize("text", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_nonfinite_decimal_is_not_a_supported_observation(text):
    with pytest.raises(CaptureLimit):
        encode(Decimal(text), Limits())
    with pytest.raises(InvalidSnapshot):
        decode({"t": "decimal", "v": text}, Limits())


@pytest.mark.parametrize("text", ["garbage", " 1", "01", "1e+2"])
def test_decimal_decoder_requires_canonical_data(text):
    with pytest.raises(InvalidSnapshot):
        decode({"t": "decimal", "v": text}, Limits())
