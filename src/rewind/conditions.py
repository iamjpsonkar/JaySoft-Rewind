"""Typed retention and a bounded grammar; expressions never execute Python."""

import math
import operator
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Retention:
    exceptions: bool = True
    status_at_least: int | None = 500
    duration_at_least: float | None = None
    always: bool = False

    def __post_init__(self) -> None:
        if self.status_at_least is not None and not 100 <= self.status_at_least <= 599:
            raise ValueError("status threshold must be an HTTP status")
        if self.duration_at_least is not None and (
            not math.isfinite(self.duration_at_least) or self.duration_at_least < 0
        ):
            raise ValueError("duration threshold cannot be negative")

    def matches(self, *, failed: bool, status: int | None, duration: float) -> bool:
        return (
            self.always
            or (self.exceptions and failed)
            or (
                self.status_at_least is not None
                and status is not None
                and status >= self.status_at_least
            )
            or (self.duration_at_least is not None and duration >= self.duration_at_least)
        )


_TOKEN = re.compile(r"\s*(>=|<=|==|!=|>|<|\(|\)|\d+(?:\.\d+)?(?:ms|s)?|[a-z]+)")
_COMPARE = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}


@dataclass(frozen=True)
class Condition:
    """Safe `exception`, status, and duration predicates with and/or/not/grouping.

    Example: ``Condition.parse('exception or (status >= 500 and duration > 20ms)')``.
    No names, calls, attributes, indexing, or arbitrary Python are evaluated.
    """

    expression: str
    _tree: Any

    @classmethod
    def parse(cls, expression: str) -> "Condition":
        if type(expression) is not str or not expression.strip() or len(expression) > 512:
            raise ValueError("condition must contain 1–512 characters")
        tokens: list[str] = []
        position = 0
        expression = expression.strip()
        while position < len(expression):
            match = _TOKEN.match(expression, position)
            if match is None:
                raise ValueError("unsupported condition syntax")
            tokens.append(match[1])
            position = match.end()
            if len(tokens) > 64:
                raise ValueError("condition has too many tokens")
        cursor = 0

        def take() -> str:
            nonlocal cursor
            if cursor >= len(tokens):
                raise ValueError("incomplete condition")
            token = tokens[cursor]
            cursor += 1
            return token

        def peek() -> str | None:
            return tokens[cursor] if cursor < len(tokens) else None

        def atom(depth: int) -> Any:
            if depth > 16:
                raise ValueError("condition nesting limit")
            token = take()
            if token == "not":
                return ("not", atom(depth + 1))
            if token == "(":
                value = disjunction(depth + 1)
                if take() != ")":
                    raise ValueError("unclosed condition group")
                return value
            if token in ("exception", "always", "never"):
                return (token,)
            if token not in ("status", "duration"):
                raise ValueError("unsupported condition field")
            comparison = take()
            if comparison not in _COMPARE:
                raise ValueError("condition requires a comparison")
            literal = take()
            if token == "status":
                if not literal.isascii() or not literal.isdigit() or not 100 <= int(literal) <= 599:
                    raise ValueError("status must be an integer from 100 to 599")
                threshold: float = int(literal)
            else:
                if not re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s)?", literal):
                    raise ValueError("duration requires nonnegative seconds or milliseconds")
                scale = 0.001 if literal.endswith("ms") else 1.0
                number = literal[:-2] if literal.endswith("ms") else literal.removesuffix("s")
                threshold = float(number) * scale
                if not math.isfinite(threshold):
                    raise ValueError("duration must be finite")
            return (token, comparison, threshold)

        def conjunction(depth: int) -> Any:
            value = atom(depth)
            while peek() == "and":
                take()
                value = ("and", value, atom(depth))
            return value

        def disjunction(depth: int) -> Any:
            value = conjunction(depth)
            while peek() == "or":
                take()
                value = ("or", value, conjunction(depth))
            return value

        tree = disjunction(0)
        if cursor != len(tokens):
            raise ValueError("unexpected condition token")
        return cls(expression, tree)

    def matches(self, *, failed: bool, status: int | None, duration: float) -> bool:
        def evaluate(node: Any) -> bool:
            field = node[0]
            if field == "exception":
                return failed
            if field in ("always", "never"):
                return field == "always"
            if field == "not":
                return not evaluate(node[1])
            if field == "and":
                return evaluate(node[1]) and evaluate(node[2])
            if field == "or":
                return evaluate(node[1]) or evaluate(node[2])
            value = status if field == "status" else duration
            return value is not None and _COMPARE[node[1]](value, node[2])

        return evaluate(self._tree)
