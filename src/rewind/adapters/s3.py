"""Explicit boto3 S3 observations, including lazy response-body reads."""

import importlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .. import context
from ..recorder import Recorder
from ..replay import ReplaySession

_METHODS = frozenset(
    ("put_object", "get_object", "head_object", "delete_object", "list_objects_v2")
)
# These are credentials even though their names are not generic policy keys.
_SECRETS = frozenset(("SSECustomerKey", "CopySourceSSECustomerKey"))


def _active() -> Recorder | ReplaySession | None:
    active = context.current.get()
    return active if isinstance(active, (Recorder, ReplaySession)) else None


def _observe(dependency: str, parameters: dict[str, Any], live: Callable[[], Any]) -> Any:
    active = _active()
    if isinstance(active, ReplaySession):
        outcome = active.consume("cloud.call", dependency, parameters)
        data = active.unpack(outcome["value"]) if outcome["kind"] == "return" else None
        if type(data) is not dict or data.get("kind") not in ("return", "client_error"):
            active.fail("unsupported cloud observation")
        if data["kind"] == "client_error":
            ClientError = importlib.import_module("botocore.exceptions").ClientError

            if type(data.get("response")) is not dict or type(data.get("operation")) is not str:
                active.fail("invalid S3 error observation")
            kind = data.get("exception", "ClientError")
            cls = ClientError
            if kind != "ClientError":
                ClientExceptionsFactory = importlib.import_module(
                    "botocore.errorfactory"
                ).ClientExceptionsFactory
                Session = importlib.import_module("botocore.session").Session

                classes = ClientExceptionsFactory().create_client_exceptions(
                    Session().get_service_model("s3")
                )
                cls = getattr(classes, kind, None)
                if not isinstance(cls, type) or not issubclass(cls, ClientError):
                    active.fail("unsupported S3 exception class")
            raise cls(data["response"], data["operation"])
        return data.get("value")
    if active is None or active.sealed:
        return live()
    if any(key in _SECRETS for key in parameters.get("kwargs", {})):
        active.mark("s3_customer_key_excluded")
        # Do not serialize customer keys or outcomes for this call.
        return live()
    body = parameters.get("kwargs", {}).get("Body")
    if body is not None and type(body) not in (bytes, str):
        active.mark("s3_upload_stream_unsupported")
        return live()
    if type(body) is bytes and not active.policy.capture_binary:
        active.mark("s3_binary_body_excluded")
        return live()
    slot = active.begin("cloud.call", dependency, parameters)
    try:
        value = live()
    except BaseException as exc:
        ClientError = importlib.import_module("botocore.exceptions").ClientError

        if isinstance(exc, ClientError):
            active.finish(
                slot,
                active.returned(
                    {
                        "kind": "client_error",
                        "exception": type(exc).__name__,
                        "response": exc.response,
                        "operation": exc.operation_name,
                    }
                ),
            )
        else:
            active.mark("s3_exception_unsupported")
            active.finish(slot, active.raised(exc))
        raise
    if type(value) is bytes and not active.policy.capture_binary:
        active.mark("s3_binary_body_excluded")
        active.finish(slot, active.returned(None))
    else:
        active.finish(slot, active.returned({"kind": "return", "value": _normalize(value)}))
    return value


class RecordingS3:
    """Wrap a configured S3 client. No client is constructed during replay.

    Supported calls: put/get/head/delete_object and list_objects_v2. Uploads
    accept bounded bytes/strings for complete capture. Multipart transfers,
    paginators, resources and arbitrary SDK methods are deliberately not exposed.
    """

    def __init__(self, client: Any = None, *, dependency: str = "s3") -> None:
        self.client = client
        self.dependency = dependency

    def __getattr__(self, name: str) -> Any:
        if name not in _METHODS:
            raise AttributeError(name)
        return lambda **kwargs: self._call(name, kwargs)

    def _call(self, name: str, kwargs: dict[str, Any]) -> Any:
        body = None

        def live() -> Any:
            nonlocal body
            if self.client is None:
                raise RuntimeError("a configured S3 client is required outside replay")
            result = getattr(self.client, name)(**kwargs)
            if name == "get_object":
                result = dict(result)
                body = result.pop("Body")
            return result

        result = _observe(self.dependency, {"method": name, "kwargs": kwargs}, live)
        if name == "get_object":
            if type(result) is not dict:
                active = _active()
                if isinstance(active, ReplaySession):
                    active.fail("invalid S3 get_object response")
                raise TypeError("invalid S3 get_object response")
            result = dict(result)
            result["Body"] = RecordingS3Body(body, self.dependency, kwargs, _active())
        return result


class RecordingS3Body:
    """Observe read/close without eagerly consuming an S3 streaming response."""

    def __init__(
        self,
        body: Any,
        dependency: str,
        request: dict[str, Any],
        owner: Recorder | ReplaySession | None,
    ) -> None:
        self._body = body
        self._dependency = dependency
        # Capture owns a detached request in the interaction. Never retain customer keys.
        self._request = {key: value for key, value in request.items() if key not in _SECRETS}
        self._owner = owner

    def _call(self, method: str, args: dict[str, Any], live: Callable[[], Any]) -> Any:
        active = _active()
        if isinstance(self._owner, ReplaySession) and active is not self._owner:
            self._owner.fail("S3 body used outside its replay scope")
        if isinstance(active, ReplaySession) and self._owner is not active:
            active.fail("S3 body belongs to another execution")
        if isinstance(self._owner, Recorder) and active is not self._owner:
            self._owner.mark("s3_body_scope_unsupported")
            if isinstance(active, Recorder):
                active.mark("s3_body_scope_unsupported")
        return _observe(
            self._dependency, {"method": "body." + method, "request": self._request, **args}, live
        )

    def read(self, amt: int | None = None) -> bytes:
        return self._call("read", {"amt": amt}, lambda: self._body.read(amt))

    def close(self) -> None:
        return self._call("close", {}, lambda: self._body.close())

    def __enter__(self) -> "RecordingS3Body":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _normalize(value: Any, depth: int = 0, count: list[int] | None = None) -> Any:
    """Replay preserves SDK timestamp instants/offsets using standard datetimes."""
    count = [0] if count is None else count
    count[0] += 1
    if depth > 32 or count[0] > 10000:
        # Leave unsupported oversized results intact; capture will mark them ineligible.
        return value
    if type(value) is datetime and value.tzinfo is not None:
        offset = value.utcoffset()
        if offset is not None:
            return value.astimezone(timezone(offset))
    if type(value) is dict:
        return {k: _normalize(v, depth + 1, count) for k, v in value.items()}
    if type(value) is list:
        return [_normalize(v, depth + 1, count) for v in value]
    return value
