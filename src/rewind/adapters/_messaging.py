"""Shared sequential, data-only observation boundary for explicit queue adapters."""

import weakref
from collections.abc import Callable
from typing import Any

from .. import context
from ..codecs import decode, encode, loads
from ..errors import RewindError
from ..recorder import Recorder
from ..replay import ReplaySession


def current() -> Recorder | ReplaySession | None:
    active = context.current.get()
    return None if isinstance(active, Recorder) and active.sealed else active


def unsupported(reason: str) -> None:
    active = current()
    if isinstance(active, ReplaySession):
        active.fail("unsupported messaging operation")
    if isinstance(active, Recorder):
        active.mark(reason)


def exception_data(
    exc: BaseException, active: Recorder, allowed: dict[str, type[BaseException]],
) -> dict[str, Any]:
    name = f"{type(exc).__module__}.{type(exc).__qualname__}"
    if name not in allowed or type(exc) is not allowed[name]:
        active.mark("messaging_exception_unsupported")
        return {"kind": "exception", "type": "unavailable", "args": encode(None, active.limits)}
    return active.raised(exc)


def exception_value(
    data: Any, active: ReplaySession, allowed: dict[str, type[BaseException]],
) -> BaseException:
    if type(data) is not dict or data.get("kind") != "exception":
        active.fail("invalid recorded messaging exception")
    kind = data.get("type")
    cls = allowed.get(kind) if type(kind) is str else None
    args = decode(data.get("args"), active.limits)
    if cls is None or type(args) is not tuple:
        active.fail("unsupported recorded messaging exception")
    return cls(*args)


def observe(
    adapter: str, dependency: str, target: str, method: str, parameters: Any,
    live: Callable[[], Any], *, allowed: dict[str, type[BaseException]],
    pack: Callable[[Any, Recorder], Any] | None = None,
    unpack: Callable[[Any, ReplaySession], Any] | None = None,
    prepare: Callable[[Any, Recorder], Any] | None = None,
    suspend: bool = False,
) -> Any:
    active = current()
    request = {"adapter": adapter, "target": target, "method": method, "parameters": parameters}
    if isinstance(active, ReplaySession):
        outcome = active.consume("messaging.call", dependency, request)
        try:
            if outcome["kind"] == "exception":
                error = exception_value(outcome, active, allowed)
            else:
                value = decode(outcome["value"], active.limits)
                return unpack(value, active) if unpack is not None else value
        except (RewindError, ValueError, TypeError, KeyError):
            active.fail("invalid recorded messaging outcome")
        raise error
    slot = None
    if isinstance(active, Recorder):
        try:
            if prepare is not None:
                request["parameters"] = prepare(parameters, active)
            slot = active.begin("messaging.call", dependency, request)
        except Exception:
            active.mark("messaging_input_capture_failed")
    # Eager Celery tasks are observed through their result handle, just as remote
    # tasks are. Their task body must not join this producer's capture context.
    token = context.current.set(None) if suspend and active is not None else None
    try:
        result = live()
    except BaseException as exc:
        if isinstance(active, Recorder):
            try:
                active.finish(slot, exception_data(exc, active, allowed))
            except Exception:
                active.mark("messaging_output_capture_failed")
        raise
    finally:
        if token is not None:
            context.current.reset(token)
    if isinstance(active, Recorder) and not active.sealed and slot is not None:
        try:
            value = pack(result, active) if pack is not None else result
            active.finish(slot, active.returned(value))
        except Exception:
            active.mark("messaging_output_capture_failed")
    return result


def headers(value: Any, active: Recorder) -> Any:
    if value is None:
        return None
    if type(value) not in (list, tuple) or len(value) > active.limits.items:
        active.mark("messaging_headers_unsupported")
        return None
    result = []
    for pair in value:
        if type(pair) not in (list, tuple) or len(pair) != 2 or type(pair[0]) is not str:
            active.mark("messaging_headers_unsupported")
            return None
        key, val = pair
        if active.policy.is_sensitive(key):
            active.mark("sensitive_message_header_removed")
            val = b"[REDACTED]" if type(val) is bytes else "[REDACTED]"
        result.append((key, val))
    return result


def message(value: Any, active: Recorder) -> Any:
    """Opaque payloads require opt-in; recognizable JSON gets named-field policy."""
    if type(value) not in (str, bytes):
        return value
    if type(value) is bytes and not active.policy.capture_binary:
        active.mark("binary_message_excluded")
        return None
    if len(value) > active.limits.body_bytes:
        active.mark("message_body_limit")
        return None
    prefix = value.lstrip()
    structured = (
        prefix.startswith((b"{", b"[")) if type(value) is bytes
        else prefix.startswith(("{", "["))
    )
    if structured:
        try:
            decoded = loads(value.encode() if type(value) is str else value, active.limits)
            changed = False

            def mark(reason: str) -> None:
                nonlocal changed
                changed = True
                active.mark(reason)

            active.policy.value(decoded, active.limits, mark)
            if changed:
                return b"[REDACTED]" if type(value) is bytes else "[REDACTED]"
        except Exception:
            active.mark("message_json_unsupported")
            return None
    return value


class OwnedHandle:
    def __init__(self, inner: Any, owner: Any, target: str) -> None:
        self._inner = inner
        self._owner = weakref.ref(owner) if owner is not None else lambda: None
        self._target = target

    def _check(self) -> None:
        active = current()
        if active is not None:
            active.check_task()
            if active is not self._owner():
                if isinstance(active, ReplaySession):
                    active.fail("messaging handle belongs to another execution")
                active.mark("messaging_handle_outside_capture")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        self._check()
        unsupported("messaging_handle_operation_unsupported")
        return getattr(self._inner, name)
