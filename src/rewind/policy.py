"""Conservative capture policy with explicit opt-in for synthetic fixtures."""

import base64
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .codecs import decode, encode, loads
from .errors import CaptureLimit, InvalidSnapshot
from .limits import Limits

Mark = Callable[[str], None]
_SENSITIVE = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
        "accesskey",
        "privatekey",
        "cardnumber",
        "cvv",
        "ssn",
    }
)


def sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "").replace("_", "")
    return normalized in _SENSITIVE or normalized.endswith(
        ("token", "secret", "password", "apikey")
    )


@dataclass(frozen=True)
class CapturePolicy:
    capture_values: bool = False
    capture_bodies: bool = False
    capture_binary: bool = False
    exception_args: bool = False

    @classmethod
    def synthetic(cls) -> "CapturePolicy":
        """Opt in to fixture data. Never use this as a production privacy guarantee."""
        return cls(True, True, True, True)

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    def _scrub(self, value: Any, mark: Mark) -> Any:
        if type(value) is dict:
            result = {}
            for key, item in value.items():
                if sensitive(key):
                    result[key] = "[REDACTED]"
                    mark("sensitive_value_removed")
                else:
                    result[key] = self._scrub(item, mark)
            return result
        if type(value) in (list, tuple):
            items = [self._scrub(item, mark) for item in value]
            return tuple(items) if type(value) is tuple else items
        return value

    def value(self, value: Any, limits: Limits, mark: Mark) -> dict[str, Any]:
        if not self.capture_values:
            if value is not None:
                mark("values_excluded")
            return encode(None, limits)
        detached = decode(encode(value, limits), limits)
        return encode(self._scrub(detached, mark), limits)

    def headers(self, pairs: list[tuple[bytes, bytes]], mark: Mark) -> list[list[str]]:
        result = []
        for name, value in pairs:
            key = name.decode("latin-1").lower()
            if sensitive(key):
                result.append([key, "[REDACTED]"])
                mark("sensitive_header_removed")
            else:
                result.append([key, value.decode("latin-1")])
        return result

    def url(self, url: str, mark: Mark) -> str:
        parts = urlsplit(url)
        netloc = parts.netloc
        changed = False
        if parts.username is not None or parts.password is not None:
            netloc = netloc.rsplit("@", 1)[-1]
            mark("url_credentials_removed")
            changed = True
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if sensitive(key):
                value = "[REDACTED]"
                mark("sensitive_query_removed")
                changed = True
            query.append((key, value))
        if changed:
            return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))
        return url

    def body(self, body: bytes, content_type: str, limits: Limits, mark: Mark) -> str:
        if not body:
            return ""
        if len(body) > limits.body_bytes:
            mark("body_limit")
            return ""
        if not self.capture_bodies:
            mark("body_excluded")
            return ""
        if "json" in content_type.lower():
            try:
                # Reject duplicate keys before inspecting a body: otherwise a later
                # key can hide a secret while the original bytes remain unchanged.
                value = loads(body, limits)
                # Bound the nested value before recursing through the policy.
                detached = decode(encode(value, limits), limits)
                dirty = False

                def changed(reason: str) -> None:
                    nonlocal dirty
                    dirty = True
                    mark(reason)

                sanitized = self._scrub(detached, changed)
                if dirty:
                    body = json.dumps(sanitized, allow_nan=False).encode()
            except (ValueError, UnicodeError, RecursionError, InvalidSnapshot, CaptureLimit):
                mark("invalid_json_body")
                return ""
        elif not self.capture_binary:
            mark("binary_body_excluded")
            return ""
        if len(body) > limits.body_bytes:
            mark("body_limit")
            return ""
        return base64.b64encode(body).decode("ascii")

    def exception(self, exc: BaseException, limits: Limits, mark: Mark) -> dict[str, Any]:
        args: Any = None
        if self.exception_args:
            args = self.value(exc.args, limits, mark)
        else:
            mark("exception_args_excluded")
            args = encode(None, limits)
        return {
            "kind": "exception",
            "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "args": args,
        }
