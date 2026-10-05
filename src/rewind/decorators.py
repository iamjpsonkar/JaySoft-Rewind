"""Conditional snapshots for ordinary functions and plain Python methods.

The decorator captures data at a call boundary. External dependencies still need
their Rewind adapters; this is not a process or arbitrary Python heap checkpoint.
"""

import functools
import inspect
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import GetSetDescriptorType, MappingProxyType, MemberDescriptorType
from typing import Any

from . import context
from .codecs import encode
from .conditions import Condition, Retention
from .core import Rewind
from .errors import ReplayDivergence
from .policy import CapturePolicy
from .recorder import Recorder
from .runner import ReplayTarget, replay_file
from .storage import LocalStore


@dataclass(frozen=True)
class Call:
    """In-memory call information supplied to a synchronous retention predicate.

    Mapping views are read-only, but referenced application values are not copied.
    Predicates must not mutate them. Duration is measured in seconds.
    """

    function: str
    args: tuple[Any, ...]
    kwargs: Mapping[str, Any]
    arguments: Mapping[str, Any]
    result: Any
    error: BaseException | None
    duration: float
    status: int | None


_NEVER = Condition.parse("never")
_CLASS_METADATA = {
    "__module__",
    "__doc__",
    "__dict__",
    "__weakref__",
    "__slots__",
    "__annotations__",
    "__qualname__",
    "__firstlineno__",
    "__static_attributes__",
}


def _class_supported(owner: type) -> bool:
    """Reject class data/global state we cannot restore without running user code."""
    if type(owner) is not type:
        return False
    for base in owner.__mro__:
        if base is object:
            continue
        if base.__module__ == "builtins":
            return False
        for name, value in vars(base).items():
            if name in {"__dict__", "__weakref__"} and not isinstance(value, GetSetDescriptorType):
                return False
            if name in _CLASS_METADATA:
                continue
            if isinstance(value, (staticmethod, classmethod, property, MemberDescriptorType)):
                continue
            if inspect.isfunction(value):
                continue
            return False
    return True


def _slots(owner: type) -> dict[str, MemberDescriptorType]:
    result = {}
    for base in reversed(owner.__mro__):
        for name, value in vars(base).items():
            if isinstance(value, MemberDescriptorType):
                if name in result:
                    raise ValueError("shadowed slots are unsupported")
                result[name] = value
    return result


def _state(receiver: Any, owner: type) -> dict[str, Any]:
    if type(receiver) is not owner:
        raise ValueError("receiver type differs from declared owner")
    try:
        dictionary = object.__getattribute__(receiver, "__dict__")
    except AttributeError:
        dictionary = None
    if dictionary is not None and type(dictionary) is not dict:
        raise ValueError("custom instance dictionaries are unsupported")
    slots = {}
    for name, descriptor in _slots(owner).items():
        try:
            slots[name] = descriptor.__get__(receiver, owner)
        except AttributeError:
            pass
    return {"dict": None if dictionary is None else dictionary.copy(), "slots": slots}


def _restore(owner: type, state: Any) -> Any:
    if type(state) is not dict or set(state) != {"dict", "slots"}:
        raise ReplayDivergence("invalid receiver state")
    dictionary, slots = state["dict"], state["slots"]
    if (
        (dictionary is not None and type(dictionary) is not dict)
        or type(slots) is not dict
        or any(type(key) is not str for key in (dictionary or {}))
        or any(type(key) is not str for key in slots)
    ):
        raise ReplayDivergence("invalid receiver state fields")
    descriptors = _slots(owner)
    if not set(slots).issubset(descriptors):
        raise ReplayDivergence("unknown receiver slot")
    receiver: Any = object.__new__(owner)
    try:
        target = object.__getattribute__(receiver, "__dict__")
    except AttributeError:
        target = None
    if (target is None) != (dictionary is None):
        raise ReplayDivergence("receiver dictionary layout differs")
    if dictionary is not None:
        target.update(dictionary)
    for name, value in slots.items():
        descriptors[name].__set__(receiver, value)
    return receiver


class CaptureHandle:
    """Management and explicit replay access attached as ``function.rewind``."""

    def __init__(
        self,
        function: Callable[..., Any],
        *,
        condition: Any,
        store: Any,
        policy: CapturePolicy | None,
        code_paths: list[str | Path] | None,
    ) -> None:
        self.function = function
        self.name = f"{function.__module__}.{function.__qualname__}"
        self.signature = inspect.signature(function)
        self.condition = Condition.parse(condition) if isinstance(condition, str) else condition
        if self.condition is not None and not isinstance(self.condition, Condition):
            if not callable(self.condition):
                raise TypeError("condition must be a callable or safe condition expression")
            if inspect.iscoroutinefunction(self.condition) or inspect.iscoroutinefunction(
                getattr(self.condition, "__call__", None)  # noqa: B004
            ):
                raise TypeError("condition must be synchronous")
        self.policy = policy or CapturePolicy(capture_values=True, exception_args=True)
        if not isinstance(self.policy, CapturePolicy):
            raise TypeError("policy must be CapturePolicy")
        self.store = store
        self.code_paths = list(code_paths) if code_paths is not None else None
        self.owner: type | None = None
        self.binding = "function"
        self.wrapper: Any = None
        self._recorder: Rewind | None = None
        self._store_ready = False
        self._lock = threading.RLock()
        self._metrics: Counter[str] = Counter(
            calls=0,
            condition_matched=0,
            condition_skipped=0,
            condition_errors=0,
            capture_errors=0,
            initialization_errors=0,
        )

    def _count(self, key: str) -> None:
        with self._lock:
            self._metrics[key] += 1

    def _new_recorder(self, *, recording: bool) -> Rewind:
        paths = self.code_paths
        if paths is None:
            source = inspect.getsourcefile(self.function)
            if source is None:
                raise ValueError("capture requires a source file or explicit code_paths")
            paths = [source]
        store = None
        if recording and self.store is not None:
            store = self.store if isinstance(self.store, LocalStore) else LocalStore(self.store)
        return Rewind(
            application=self.name,
            code_paths=paths,
            store=store,
            policy=self.policy,
            retain=Retention(always=True),
        )

    @property
    def recorder(self) -> Rewind:
        """Build the advanced adapter API without creating a snapshot directory.

        Storage is attached when this decorated function is first called outside
        another capture or replay. Accessing adapters during imports is safe.
        """
        with self._lock:
            if self._recorder is None:
                self._recorder = self._new_recorder(recording=False)
            return self._recorder

    def stats(self) -> dict[str, int | bool]:
        with self._lock:
            result: dict[str, int | bool] = dict(self._metrics)
            if self._recorder is not None:
                result.update(self._recorder.stats())
            return result

    def _binding(self, args: tuple[Any, ...]) -> tuple[str, type | None]:
        if self.owner is not None:
            return self.binding, self.owner
        if args:
            candidate = args[0] if isinstance(args[0], type) else type(args[0])
            for base in candidate.__mro__:
                descriptor = vars(base).get(self.function.__name__)
                original = (
                    descriptor.__func__
                    if isinstance(descriptor, (classmethod, staticmethod))
                    else descriptor
                )
                if original is self.wrapper:
                    kind = "class" if isinstance(descriptor, classmethod) else "instance"
                    if isinstance(descriptor, staticmethod):
                        kind = "function"
                    return kind, base
        return "function", None

    def _capture_state(self, recorder: Recorder, receiver: Any, owner: type) -> Any:
        try:
            if not _class_supported(owner):
                raise ValueError("receiver class state is unsupported")
            state = _state(receiver, owner)
            encode(state, recorder.limits)
            return state
        except Exception:
            recorder.mark("receiver_state_unsupported")
            return None

    def _prepare(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self._count("calls")
        if context.current.get() is not None:
            return None
        try:
            rewind = self.recorder
            with self._lock:
                if not self._store_ready:
                    if self.store is not None:
                        rewind.store = (
                            self.store
                            if isinstance(self.store, LocalStore)
                            else LocalStore(self.store)
                        )
                    self._store_ready = True
        except Exception:
            self._count("initialization_errors")
            return None
        try:
            recorder = rewind.start(
                "callable" if inspect.iscoroutinefunction(self.function) else "callable_sync"
            )
        except Exception:
            self._count("capture_errors")
            return None
        if recorder is None:
            return None
        kind, owner, receiver = "function", None, None
        try:
            bound = self.signature.bind(*args, **kwargs)
            bound.apply_defaults()
            kind, owner = self._binding(bound.args)
            receiver = bound.args[0] if kind == "instance" else None
            arguments = dict(bound.arguments)
            if kind != "function":
                arguments.pop(next(iter(self.signature.parameters)))
            if owner is not None and not _class_supported(owner):
                recorder.mark("class_state_unsupported")
            state = (
                self._capture_state(recorder, receiver, owner)
                if (kind == "instance" and owner is not None)
                else None
            )
            envelope = {"version": 1, "kind": kind, "arguments": arguments, "receiver": state}
            recorder.set_input({"args": (envelope,), "kwargs": {}})
        except Exception:
            recorder.mark("call_arguments_unsupported")
            arguments = {}
        return rewind, recorder, kind, owner, receiver, arguments, time.monotonic()

    def _outcome(
        self,
        result: Any,
        error: BaseException | None,
        state: Any,
    ) -> dict[str, Any]:
        failure = None
        if error is not None:
            failure = {
                "type": f"{type(error).__module__}.{type(error).__qualname__}",
                "args": error.args if self.policy.exception_args else None,
            }
        return {"result": result, "error": failure, "receiver": state}

    def _finish(
        self,
        prepared: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
        error: BaseException | None,
    ) -> None:
        rewind, recorder, kind, owner, receiver, arguments, started = prepared
        retain = False
        outcome = recorder.returned(None)
        try:
            status = (
                result.get("status_code", result.get("status"))
                if type(result) is dict
                else inspect.getattr_static(result, "status_code", None)
            )
            if type(status) is not int or not 100 <= status <= 599:
                status = None
            call = Call(
                self.name,
                args[1:] if kind != "function" else args,
                MappingProxyType(kwargs),
                MappingProxyType(arguments),
                result,
                error,
                max(0.0, time.monotonic() - started),
                status,
            )
            try:
                if self.condition is None:
                    retain = True
                elif isinstance(self.condition, Condition):
                    retain = self.condition.matches(
                        failed=error is not None,
                        status=status,
                        duration=call.duration,
                    )
                else:
                    decision = self.condition(call)
                    if inspect.iscoroutine(decision):
                        decision.close()
                    if type(decision) is not bool:
                        raise TypeError("condition must return bool")
                    retain = decision
            except BaseException:
                self._count("condition_errors")
            self._count("condition_matched" if retain else "condition_skipped")
            if retain:
                if error is not None and not isinstance(error, Exception):
                    recorder.mark("execution_interrupted")
                if error is not None and not self.policy.exception_args:
                    recorder.mark("exception_args_excluded")
                state = (
                    self._capture_state(recorder, receiver, owner) if kind == "instance" else None
                )
                outcome = recorder.returned(self._outcome(result, error, state))
        except Exception:
            self._count("capture_errors")
            recorder.mark("decorator_capture_failed")
        finally:
            rewind.finish(
                recorder,
                outcome,
                failed=error is not None,
                retain=Retention(always=True) if retain else _NEVER,
            )

    def _decode_call(self, envelope: Any, owner: type | None) -> tuple[Any, Any]:
        if (
            type(envelope) is not dict
            or set(envelope) != {"version", "kind", "arguments", "receiver"}
            or type(envelope["version"]) is not int
            or envelope["version"] != 1
            or envelope["kind"] not in {"function", "instance", "class"}
            or type(envelope["arguments"]) is not dict
        ):
            raise ReplayDivergence("invalid decorated call envelope")
        kind = envelope["kind"]
        expected = self.binding
        if self.owner is None and owner is not None:
            for base in owner.__mro__:
                descriptor = vars(base).get(self.function.__name__)
                function = (
                    descriptor.__func__
                    if isinstance(descriptor, (classmethod, staticmethod))
                    else descriptor
                )
                if function is self.wrapper:
                    expected = (
                        "class"
                        if isinstance(descriptor, classmethod)
                        else "function"
                        if isinstance(descriptor, staticmethod)
                        else "instance"
                    )
                    break
            else:
                raise ReplayDivergence("trusted class does not declare decorated method")
        if kind != expected:
            raise ReplayDivergence("decorator binding differs")
        arguments = envelope["arguments"].copy()
        parameters = self.signature.parameters
        if any(type(name) is not str or name not in parameters for name in arguments):
            raise ReplayDivergence("invalid argument names")
        receiver = None
        if kind != "instance" and envelope["receiver"] is not None:
            raise ReplayDivergence("unexpected receiver state")
        if kind != "function":
            if owner is None or not _class_supported(owner) or not parameters:
                raise ReplayDivergence("trusted receiver class is required")
            name = next(iter(parameters))
            if name in arguments:
                raise ReplayDivergence("receiver cannot be supplied as an argument")
            receiver = _restore(owner, envelope["receiver"]) if kind == "instance" else owner
            arguments = {name: receiver, **arguments}
        for name, parameter in parameters.items():
            if name not in arguments:
                continue
            value = arguments[name]
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL and type(value) is not tuple:
                raise ReplayDivergence("variadic positional arguments must be a tuple")
            if parameter.kind is inspect.Parameter.VAR_KEYWORD and (
                type(value) is not dict or any(type(key) is not str for key in value)
            ):
                raise ReplayDivergence("variadic keyword arguments must be a string-keyed mapping")
        bound = inspect.BoundArguments(self.signature, arguments)
        try:
            checked = self.signature.bind(*bound.args, **bound.kwargs)
            checked.apply_defaults()
            if set(checked.arguments) != set(arguments):
                raise TypeError("incomplete argument mapping")
        except (TypeError, ValueError) as exc:
            raise ReplayDivergence("invalid decorated arguments") from exc
        return checked, receiver if kind == "instance" else None

    def replay_target(self, owner: type | None = None) -> ReplayTarget:
        """Build a target without constructing an application receiver or store."""
        owner = self.owner or owner
        rewind = self._new_recorder(recording=False)

        def state(receiver: Any) -> Any:
            return _state(receiver, owner) if receiver is not None and owner is not None else None

        if inspect.iscoroutinefunction(self.function):

            async def asynchronous(envelope: Any) -> Any:
                bound, receiver = self._decode_call(envelope, owner)
                try:
                    result = await self.function(*bound.args, **bound.kwargs)
                except ReplayDivergence:
                    raise
                except Exception as exc:
                    return self._outcome(None, exc, state(receiver))
                return self._outcome(result, None, state(receiver))

            return ReplayTarget(rewind, asynchronous, "callable")

        def synchronous(envelope: Any) -> Any:
            bound, receiver = self._decode_call(envelope, owner)
            try:
                result = self.function(*bound.args, **bound.kwargs)
            except ReplayDivergence:
                raise
            except Exception as exc:
                return self._outcome(None, exc, state(receiver))
            return self._outcome(result, None, state(receiver))

        return ReplayTarget(rewind, synchronous, "callable_sync")

    def replay(
        self, path: str | Path, *, timeout: float = 30, isolation: str = "python-guard"
    ) -> Any:
        """Replay using this explicitly selected importable decorated entry point."""
        function = self.function
        if function.__module__ == "__main__" or "<locals>" in function.__qualname__:
            raise ValueError("replay requires a function or class method in an importable module")
        return replay_file(
            path,
            f"{function.__module__}:{function.__qualname__}",
            timeout=timeout,
            isolation=isolation,
        )


def capture(
    function_or_class: Any = None,
    *,
    condition: Any = None,
    store: str | Path | LocalStore | None = ".rewind/snapshots",
    policy: CapturePolicy | None = None,
    code_paths: list[str | Path] | None = None,
) -> Any:
    """Save matching calls; with no condition save every call.

    Decorated classes retain their identity and constructors. Only their declared
    public methods are wrapped. State must contain supported data-only values.
    """

    def decorate(target: Any) -> Any:
        if isinstance(target, type):
            methods = []
            for name, descriptor in list(vars(target).items()):
                if name.startswith("_"):
                    continue
                function = (
                    descriptor.__func__
                    if isinstance(descriptor, (staticmethod, classmethod))
                    else descriptor
                )
                if not inspect.isfunction(function):
                    continue
                if isinstance(getattr(function, "rewind", None), CaptureHandle):
                    raise TypeError("decorate either the class or its individual methods, not both")
                if inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
                    raise TypeError("generator methods are not supported by capture")
                methods.append((name, descriptor, function))
            # Validate before changing any method on the original class.
            for name, descriptor, function in methods:
                wrapped = decorate(function)
                wrapped.rewind.owner = target
                wrapped.rewind.binding = (
                    "class"
                    if isinstance(descriptor, classmethod)
                    else "function"
                    if isinstance(descriptor, staticmethod)
                    else "instance"
                )
                if isinstance(descriptor, (staticmethod, classmethod)):
                    wrapped = type(descriptor)(wrapped)
                setattr(target, name, wrapped)
            return target
        if isinstance(target, (staticmethod, classmethod)):
            return type(target)(decorate(target.__func__))
        if not inspect.isfunction(target):
            raise TypeError("capture requires a Python function or class")
        if isinstance(getattr(target, "rewind", None), CaptureHandle):
            raise TypeError("function already has a capture decorator")
        if inspect.isgeneratorfunction(target) or inspect.isasyncgenfunction(target):
            raise TypeError("generator functions are not supported by capture")
        handle = CaptureHandle(
            target, condition=condition, store=store, policy=policy, code_paths=code_paths
        )
        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def asynchronous(*args: Any, **kwargs: Any) -> Any:
                prepared = handle._prepare(args, kwargs)
                if prepared is None:
                    return await target(*args, **kwargs)
                token = context.current.set(prepared[1])
                result, error = None, None
                try:
                    result = await target(*args, **kwargs)
                    return result
                except BaseException as exc:
                    error = exc
                    raise
                finally:
                    context.current.reset(token)
                    handle._finish(prepared, args, kwargs, result, error)

            wrapper: Any = asynchronous
        else:

            @functools.wraps(target)
            def synchronous(*args: Any, **kwargs: Any) -> Any:
                prepared = handle._prepare(args, kwargs)
                if prepared is None:
                    return target(*args, **kwargs)
                token = context.current.set(prepared[1])
                result, error = None, None
                try:
                    result = target(*args, **kwargs)
                    return result
                except BaseException as exc:
                    error = exc
                    raise
                finally:
                    context.current.reset(token)
                    handle._finish(prepared, args, kwargs, result, error)

            wrapper = synchronous
        wrapper.rewind = handle
        handle.wrapper = wrapper
        return wrapper

    return decorate if function_or_class is None else decorate(function_or_class)
