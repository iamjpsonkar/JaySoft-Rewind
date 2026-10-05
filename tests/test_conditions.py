import pytest

from rewind import Condition, Retention


@pytest.mark.parametrize(
    "expression,failed,status,duration,expected",
    [
        ("exception", True, None, 0, True),
        ("status >= 500", False, None, 0, False),
        ("status >= 500", False, 503, 0, True),
        ("status != 200", False, 200, 0, False),
        ("duration > 20ms", False, 200, 0.021, True),
        ("duration > 2s", False, 200, 2, False),
        ("exception or status >= 500 and duration > 1", True, 200, 0, True),
        ("(exception or status >= 500) and duration > 1", True, 200, 0, False),
        ("not exception and always", False, 200, 0, True),
        ("never", True, 500, 10, False),
    ],
)
def test_condition_grammar(expression, failed, status, duration, expected):
    assert (
        Condition.parse(expression).matches(failed=failed, status=status, duration=duration)
        is expected
    )


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "__import__('os').system('echo x')",
        "status[0]",
        "duration > float('inf')",
        "status >= 700",
        "status == 200.0",
        "duration < -1",
        "status >= 500 or",
        "(exception",
        "exception)",
        "duration > 1ms junk",
        "a" * 513,
        "not " * 65 + "exception",
        "(" * 20 + "exception" + ")" * 20,
    ],
)
def test_rejects_unsupported_and_unbounded_conditions(expression):
    with pytest.raises(ValueError):
        Condition.parse(expression)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_typed_retention_requires_finite_duration(value):
    with pytest.raises(ValueError):
        Retention(duration_at_least=value)
