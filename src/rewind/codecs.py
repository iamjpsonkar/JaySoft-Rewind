"""Small, bounded, data-only codec. Never invokes arbitrary object methods."""

import base64
import binascii
import json
import math
from typing import Any

from .errors import CaptureLimit, InvalidSnapshot
from .limits import Limits


def dumps(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()


def loads(raw: bytes, limits: Limits) -> Any:
    if len(raw) > limits.snapshot_bytes:
        raise InvalidSnapshot("artifact exceeds byte limit")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidSnapshot("duplicate JSON key")
            result[key] = value
        return result

    def bad_constant(_: str) -> None:
        raise InvalidSnapshot("non-finite JSON number")

    try:
        result = json.loads(raw, object_pairs_hook=unique, parse_constant=bad_constant)
        count = 0

        def check(value: Any, depth: int) -> None:
            nonlocal count
            count += 1
            if depth > limits.depth * 3 + 12 or count > limits.items * 8:
                raise InvalidSnapshot("artifact exceeds structural limit")
            if type(value) is float and not math.isfinite(value):
                raise InvalidSnapshot("non-finite JSON number")
            if isinstance(value, (dict, list)):
                for item in value.values() if isinstance(value, dict) else value:
                    check(item, depth + 1)

        check(result, 0)
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidSnapshot("invalid JSON artifact") from exc


def encode(value: Any, limits: Limits) -> dict[str, Any]:
    """Detach built-in values; tags cannot collide with user dictionaries."""
    count = 0
    size = 0

    def visit(item: Any, depth: int) -> dict[str, Any]:
        nonlocal count, size
        count += 1
        size += 24
        if count > limits.items or depth > limits.depth or size > limits.snapshot_bytes:
            raise CaptureLimit("value exceeds structural limit")
        kind = type(item)
        if kind in (str, bytes):
            size += len(item) * (6 if kind is str else 2)
            if size > limits.snapshot_bytes:
                raise CaptureLimit("value exceeds byte limit")
        if item is None or kind in (str, bool, int):
            if kind is int and item.bit_length() > 4096:
                raise CaptureLimit("integer exceeds bit limit")
            return {"t": "scalar", "v": item}
        if kind is float and math.isfinite(item):
            return {"t": "scalar", "v": item}
        if kind is bytes:
            return {"t": "bytes", "v": base64.b64encode(item).decode("ascii")}
        if kind in (list, tuple):
            return {
                "t": "tuple" if kind is tuple else "list",
                "v": [visit(v, depth + 1) for v in item],
            }
        if kind is dict:
            if any(type(k) is not str for k in item):
                raise CaptureLimit("dictionary keys must be strings")
            return {
                "t": "dict",
                "v": [[visit(k, depth + 1), visit(v, depth + 1)] for k, v in item.items()],
            }
        raise CaptureLimit("unsupported value type")

    result = visit(value, 0)
    if len(dumps(result)) > limits.snapshot_bytes:
        raise CaptureLimit("encoded value exceeds byte limit")
    return result


def decode(value: Any, limits: Limits) -> Any:
    count = 0

    def visit(node: Any, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > limits.items or depth > limits.depth:
            raise InvalidSnapshot("codec structural limit")
        if type(node) is not dict or set(node) != {"t", "v"}:
            raise InvalidSnapshot("invalid codec envelope")
        tag, item = node["t"], node["v"]
        if tag == "scalar":
            if item is None or type(item) in (str, bool, int):
                if type(item) is int and item.bit_length() > 4096:
                    raise InvalidSnapshot("integer exceeds bit limit")
                return item
            if type(item) is float and math.isfinite(item):
                return item
        elif tag == "bytes" and type(item) is str:
            try:
                result = base64.b64decode(item, validate=True)
                if len(result) > limits.snapshot_bytes:
                    raise InvalidSnapshot("decoded bytes exceed limit")
                return result
            except (ValueError, binascii.Error) as exc:
                raise InvalidSnapshot("invalid encoded bytes") from exc
        elif tag in ("list", "tuple") and type(item) is list:
            items = [visit(v, depth + 1) for v in item]
            return tuple(items) if tag == "tuple" else items
        elif tag == "dict" and type(item) is list:
            result_dict: dict[str, Any] = {}
            for pair in item:
                if type(pair) is not list or len(pair) != 2:
                    raise InvalidSnapshot("invalid dictionary item")
                key = visit(pair[0], depth + 1)
                if type(key) is not str or key in result_dict:
                    raise InvalidSnapshot("duplicate or invalid dictionary key")
                result_dict[key] = visit(pair[1], depth + 1)
            return result_dict
        raise InvalidSnapshot("unsupported codec tag or value")

    return visit(value, 0)
