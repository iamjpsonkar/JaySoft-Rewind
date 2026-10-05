"""Explicit Celery task submissions and sequential result-handle observations."""

from typing import Any

from celery.exceptions import TaskRevokedError, TimeoutError  # type: ignore[import-untyped]
from kombu.exceptions import OperationalError  # type: ignore[import-untyped]

from ..recorder import Recorder
from ..replay import ReplaySession
from ._messaging import (
    OwnedHandle,
    current,
    exception_data,
    exception_value,
    observe,
    unsupported,
)

_ERROR_CLASSES = (
    TimeoutError, TaskRevokedError, OperationalError,
    ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, RuntimeError,
)
_ERRORS = {f"{cls.__module__}.{cls.__qualname__}": cls for cls in _ERROR_CLASSES}
_OPTIONS = frozenset({
    "task_id", "countdown", "eta", "expires", "queue", "routing_key", "exchange",
    "priority", "serializer", "compression", "time_limit", "soft_time_limit",
    "headers", "ignore_result", "retry", "retry_policy", "delivery_mode",
})


def _result(value: Any, active: Recorder) -> Any:
    if isinstance(value, BaseException):
        return {"kind": "error_value", "value": exception_data(value, active, _ERRORS)}
    return {"kind": "value", "value": value}


def _restore_result(data: Any, active: ReplaySession) -> Any:
    if type(data) is not dict or set(data) != {"kind", "value"}:
        active.fail("invalid recorded Celery result")
    if data["kind"] == "error_value":
        return exception_value(data["value"], active, _ERRORS)
    if data["kind"] != "value":
        active.fail("invalid recorded Celery result kind")
    return data["value"]


class RecordingCeleryResult(OwnedHandle):
    """Observation proxy, not a replacement worker or a distributed task scheduler."""

    def __init__(self, inner: Any, owner: Any, identifier: str, dependency: str) -> None:
        super().__init__(inner, owner, f"result:{identifier}")
        self._identifier = identifier
        self._dependency = dependency

    @property
    def id(self) -> str:
        self._check()
        return self._identifier

    @property
    def state(self) -> str:
        self._check()
        return observe(
            "celery", self._dependency, self._target, "state", {},
            lambda: self._inner.state, allowed=_ERRORS,
        )

    @property
    def status(self) -> str:
        self._check()
        return observe(
            "celery", self._dependency, self._target, "status", {},
            lambda: self._inner.status, allowed=_ERRORS,
        )

    @property
    def result(self) -> Any:
        self._check()
        return observe(
            "celery", self._dependency, self._target, "result", {},
            lambda: self._inner.result, allowed=_ERRORS, pack=_result, unpack=_restore_result,
        )

    def get(
        self, timeout: float | None = None, propagate: bool = True, interval: float = 0.5,
        no_ack: bool = True, follow_parents: bool = True, disable_sync_subtasks: bool = True,
        **options: Any,
    ) -> Any:
        self._check()
        if options:
            unsupported("celery_result_callbacks_unsupported")
        parameters = {
            "timeout": timeout, "propagate": propagate, "interval": interval,
            "no_ack": no_ack, "follow_parents": follow_parents,
            "disable_sync_subtasks": disable_sync_subtasks, **options,
        }
        return observe(
            "celery", self._dependency, self._target, "get", parameters,
            lambda: self._inner.get(**parameters), allowed=_ERRORS,
            pack=_result, unpack=_restore_result,
        )

    def ready(self) -> bool:
        self._check()
        return observe(
            "celery", self._dependency, self._target, "ready", {},
            lambda: self._inner.ready(), allowed=_ERRORS,
        )

    def successful(self) -> bool:
        self._check()
        return observe(
            "celery", self._dependency, self._target, "successful", {},
            lambda: self._inner.successful(), allowed=_ERRORS,
        )

    def failed(self) -> bool:
        self._check()
        return observe(
            "celery", self._dependency, self._target, "failed", {},
            lambda: self._inner.failed(), allowed=_ERRORS,
        )

    def forget(self) -> None:
        self._check()
        return observe(
            "celery", self._dependency, self._target, "forget", {},
            lambda: self._inner.forget(), allowed=_ERRORS,
        )


class RecordingCelery:
    """Wrap a configured Celery app; pass no app for dependency-free local replay."""

    def __init__(self, inner: Any = None, *, dependency: str = "celery") -> None:
        self.inner = inner
        self.dependency = dependency

    def _submit(
        self, method: str, name: str, args: Any, kwargs: Any, options: dict[str, Any],
    ) -> RecordingCeleryResult:
        if set(options) - _OPTIONS:
            unsupported("celery_submission_options_unsupported")
        owner = current()
        parameters = {"name": name, "args": args, "kwargs": kwargs, "options": options}

        def live() -> RecordingCeleryResult:
            if method == "send_task":
                result = self.inner.send_task(name, args=args, kwargs=kwargs, **options)
            else:
                result = self.inner.tasks[name].apply_async(args=args, kwargs=kwargs, **options)
            return RecordingCeleryResult(result, owner, result.id, self.dependency)

        def restore(data: Any, active: ReplaySession) -> RecordingCeleryResult:
            if type(data) is not dict or set(data) != {"id"} or type(data["id"]) is not str:
                active.fail("invalid recorded Celery task identifier")
            return RecordingCeleryResult(None, active, data["id"], self.dependency)

        return observe(
            "celery", self.dependency, "application", method, parameters, live,
            allowed=_ERRORS, suspend=True,
            pack=lambda result, active: {"id": result._identifier}, unpack=restore,
        )

    def send_task(
        self, name: str, args: Any = None, kwargs: Any = None, **options: Any,
    ) -> RecordingCeleryResult:
        return self._submit("send_task", name, args, kwargs, options)

    def task(self, name: str) -> "RecordingCeleryTask":
        return RecordingCeleryTask(self, name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        unsupported("celery_application_operation_unsupported")
        return getattr(self.inner, name)


class RecordingCeleryTask:
    def __init__(self, application: RecordingCelery, name: str) -> None:
        self._application = application
        self.name = name

    def apply_async(
        self, args: Any = None, kwargs: Any = None, **options: Any,
    ) -> RecordingCeleryResult:
        return self._application._submit("apply_async", self.name, args, kwargs, options)

    def delay(self, *args: Any, **kwargs: Any) -> RecordingCeleryResult:
        return self.apply_async(args=args, kwargs=kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        unsupported("celery_task_operation_unsupported")
        return getattr(self._application.inner.tasks[self.name], name)
