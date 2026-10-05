"""Public decorator behavior, including replay in a separate interpreter."""

import importlib
import inspect
import os
import uuid
from pathlib import Path

import pytest

from rewind import LocalStore, capture, replay_file


def snapshots(directory):
    return list(Path(directory).glob("*.rewind.json"))


def load_one(directory):
    paths = snapshots(directory)
    assert len(paths) == 1
    return LocalStore(directory).load(paths[0].name.removesuffix(".rewind.json"))


def module_fixture(tmp_path, monkeypatch, source):
    name = "decorator_fixture_" + uuid.uuid4().hex
    (tmp_path / (name + ".py")).write_text(
        "from rewind import capture\n"
        f"STORE = {str(tmp_path / 'snapshots')!r}\n" + source
    )
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(tmp_path), str(root), str(root / "src"))))
    return importlib.import_module(name)


def test_result_condition_and_named_arguments(tmp_path):
    observed = []

    def condition(call):
        observed.append((call.arguments["quantity"], call.result, call.error, call.duration))
        return call.result["success"] is False

    @capture(condition=condition, store=tmp_path / "snapshots")
    def reserve(quantity=1):
        return {"success": quantity < 5}

    assert reserve(2) == {"success": True}
    assert not snapshots(tmp_path / "snapshots")
    assert reserve(quantity=7) == {"success": False}
    assert load_one(tmp_path / "snapshots").complete
    assert observed[1][:3] == (7, {"success": False}, None)
    assert observed[1][3] >= 0
    assert str(inspect.signature(reserve)) == "(quantity=1)"


def test_no_condition_retains_success_and_preserves_original_exception(tmp_path):
    original = ValueError("fixture failure")

    @capture(store=tmp_path / "snapshots")
    def operation(fail=False):
        if fail:
            raise original
        return 8

    assert operation() == 8
    with pytest.raises(ValueError) as raised:
        operation(True)
    assert raised.value is original
    assert len(snapshots(tmp_path / "snapshots")) == 2


@pytest.mark.parametrize("fail", [False, True])
def test_broken_condition_cannot_change_application_behavior(tmp_path, fail):
    def broken(call):
        raise LookupError("condition error")

    @capture(condition=broken, store=tmp_path / "snapshots")
    def operation():
        if fail:
            raise ValueError("application error")
        return 42

    if fail:
        with pytest.raises(ValueError, match="application error"):
            operation()
    else:
        assert operation() == 42
    assert not snapshots(tmp_path / "snapshots")
    assert operation.rewind.stats()["condition_errors"] == 1


async def test_async_function_keeps_coroutine_identity_and_exception_condition(tmp_path):
    @capture(condition=lambda call: call.error is not None, store=tmp_path / "snapshots")
    async def divide(value):
        return 10 / value

    assert inspect.iscoroutinefunction(divide)
    assert await divide(2) == 5
    assert not snapshots(tmp_path / "snapshots")
    with pytest.raises(ZeroDivisionError):
        await divide(0)
    assert load_one(tmp_path / "snapshots").complete


def test_fresh_replay_uses_precall_mutable_arguments_without_factory(tmp_path, monkeypatch):
    module = module_fixture(tmp_path, monkeypatch, """
@capture(store=STORE)
def pop_and_fail(items):
    return 10 / items.pop()
""")
    items = [0]
    with pytest.raises(ZeroDivisionError):
        module.pop_and_fail(items)
    assert items == []
    artifact = snapshots(tmp_path / "snapshots")[0]
    report = replay_file(artifact, module.__name__ + ":pop_and_fail")
    assert report.status == "reproduced", report
    assert snapshots(tmp_path / "snapshots") == [artifact]


def test_class_replay_restores_state_without_constructor(tmp_path, monkeypatch):
    marker = tmp_path / "constructor-calls"
    module = module_fixture(tmp_path, monkeypatch, f"""
from pathlib import Path
@capture(store=STORE)
class Counter:
    def __init__(self, count):
        with Path({str(marker)!r}).open('a') as stream:
            stream.write('constructed\\n')
        self.count = count
    def increment(self, amount):
        self.count += amount
        return self.count
""")
    item = module.Counter(4)
    assert isinstance(item, module.Counter)
    assert not snapshots(tmp_path / "snapshots")
    assert item.increment(3) == 7
    artifact = snapshots(tmp_path / "snapshots")[0]
    report = replay_file(artifact, module.__name__ + ":Counter.increment")
    assert report.status == "reproduced", report
    assert item.count == 7
    assert marker.read_text() == "constructed\n"


def test_individually_decorated_instance_method_replays(tmp_path, monkeypatch):
    module = module_fixture(tmp_path, monkeypatch, """
class Wallet:
    def __init__(self):
        self.balance = 20
    @capture(condition=lambda call: call.error is not None, store=STORE)
    def debit(self, amount):
        self.balance -= amount
        if self.balance < 0:
            raise ValueError('insufficient balance')
        return self.balance
""")
    wallet = module.Wallet()
    with pytest.raises(ValueError):
        wallet.debit(25)
    artifact = snapshots(tmp_path / "snapshots")[0]
    report = replay_file(artifact, module.__name__ + ":Wallet.debit")
    assert report.status == "reproduced", report
    assert wallet.balance == -5


def test_class_static_and_class_methods_keep_calling_convention(tmp_path, monkeypatch):
    module = module_fixture(tmp_path, monkeypatch, """
@capture(store=STORE)
class Calculator:
    @staticmethod
    def double(value):
        return value * 2
    @classmethod
    def triple(cls, value):
        return value * 3
""")
    assert module.Calculator.double(2) == 4
    first = set(snapshots(tmp_path / "snapshots"))
    assert module.Calculator().triple(2) == 6
    second = set(snapshots(tmp_path / "snapshots")) - first
    assert replay_file(first.pop(), module.__name__ + ":Calculator.double").status == "reproduced"
    assert replay_file(second.pop(), module.__name__ + ":Calculator.triple").status == "reproduced"


@pytest.mark.parametrize("keyword", [False, True])
def test_sensitive_named_arguments_are_redacted_even_when_positional(tmp_path, keyword):
    @capture(store=tmp_path / "snapshots")
    def authenticate(password):
        return True

    secret = "private-fixture-password-123"
    assert (authenticate(password=secret) if keyword else authenticate(secret)) is True
    snapshot = load_one(tmp_path / "snapshots")
    assert secret.encode() not in snapshot.raw
    assert not snapshot.complete


def test_unsupported_instance_member_still_runs_and_saves_incomplete(tmp_path):
    @capture(store=tmp_path / "snapshots")
    class Service:
        def __init__(self):
            self.connection = object()

        def calculate(self, value):
            return value + 1

    assert Service().calculate(4) == 5
    assert not load_one(tmp_path / "snapshots").complete


def test_nested_decorators_keep_one_outer_snapshot(tmp_path):
    @capture(store=tmp_path / "snapshots")
    def inner(value):
        return value + 1

    @capture(store=tmp_path / "snapshots")
    def outer(value):
        return inner(value) * 2

    assert outer(4) == 10
    assert len(snapshots(tmp_path / "snapshots")) == 1


def test_replay_guard_cannot_be_swallowed_by_decorated_code(tmp_path, monkeypatch):
    module = module_fixture(tmp_path, monkeypatch, """
import socket
@capture(store=STORE)
def tries_network():
    try:
        socket.getaddrinfo('127.0.0.1', 1)
    except Exception:
        pass
    return 'finished'
""")
    assert module.tries_network() == "finished"
    report = replay_file(snapshots(tmp_path / "snapshots")[0], module.__name__ + ":tries_network")
    assert report.status == "replay_error"
    assert "blocked" in report.detail


def test_generators_are_rejected_before_deferred_execution(tmp_path):
    def generate():
        yield 1

    with pytest.raises((TypeError, ValueError)):
        capture(generate, store=tmp_path / "snapshots")


async def test_async_method_and_handle_replay(tmp_path, monkeypatch):
    module = module_fixture(tmp_path, monkeypatch, """
@capture(store=STORE)
class AsyncCounter:
    def __init__(self):
        self.count = 2
    async def increment(self, amount):
        self.count += amount
        return self.count
""")
    instance = module.AsyncCounter()
    assert inspect.iscoroutinefunction(instance.increment)
    assert await instance.increment(4) == 6
    artifact = snapshots(tmp_path / "snapshots")[0]
    assert instance.increment.rewind.replay(artifact).status == "reproduced"
    assert instance.count == 6


def test_keyword_receiver_binding_and_slots(tmp_path, monkeypatch):
    module = module_fixture(tmp_path, monkeypatch, """
class Slotted:
    __slots__ = ('count',)
    def __init__(self):
        self.count = 2
    @capture(store=STORE)
    def increment(self, amount):
        self.count += amount
        return self.count
""")
    item = module.Slotted()
    assert module.Slotted.increment(self=item, amount=4) == 6
    report = replay_file(
        snapshots(tmp_path / "snapshots")[0], module.__name__ + ":Slotted.increment"
    )
    assert report.status == "reproduced", report


@pytest.mark.parametrize("condition", [lambda call: "truthy", lambda call: None])
def test_non_boolean_conditions_do_not_silently_retain(tmp_path, condition):
    @capture(condition=condition, store=tmp_path / "snapshots")
    def operation():
        return 8

    assert operation() == 8
    assert not snapshots(tmp_path / "snapshots")
    assert operation.rewind.stats()["condition_errors"] == 1


def test_async_condition_rejected_at_decoration(tmp_path):
    async def condition(call):
        return True

    with pytest.raises(TypeError, match="synchronous"):
        capture(lambda: 8, condition=condition, store=tmp_path / "snapshots")


def test_shared_class_data_is_explicitly_ineligible(tmp_path):
    @capture(store=tmp_path / "snapshots")
    class Service:
        count = 0

        @classmethod
        def increment(cls):
            cls.count += 1
            return cls.count

    assert Service.increment() == 1
    snapshot = load_one(tmp_path / "snapshots")
    assert not snapshot.complete
    assert "class_state_unsupported" in snapshot.data["capture"]["ineligible_reasons"]


def test_safe_expression_and_bare_decorator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    @capture
    def always(value):
        return value

    @capture(condition="status >= 400", store=tmp_path / "conditional")
    def response(status):
        return {"status": status}

    assert always(7) == 7
    assert load_one(tmp_path / ".rewind/snapshots").complete
    assert response(200) == {"status": 200}
    assert not snapshots(tmp_path / "conditional")
    assert response(404) == {"status": 404}
    assert load_one(tmp_path / "conditional").complete


def test_store_failure_preserves_application_result(tmp_path):
    occupied = tmp_path / "not-a-directory"
    occupied.write_text("keep")

    @capture(store=occupied)
    def operation():
        return 8

    assert operation() == 8
    assert occupied.read_text() == "keep"
    assert operation.rewind.stats()["initialization_errors"] == 1


def test_recorded_dependency_is_reused_without_live_provider(tmp_path, monkeypatch):
    module = module_fixture(tmp_path, monkeypatch, """
import os
def provider():
    if os.environ.get('DECORATOR_FORBID_PROVIDER') == '1':
        raise AssertionError('live provider called during replay')
    return {'success': True}
@capture(store=STORE)
def operation():
    reply = operation.rewind.recorder.value('provider.reply', provider)
    return reply['receipt_id']
# Constructing adapters during module import must not create a snapshot directory.
recorder = operation.rewind.recorder
""")
    assert not (tmp_path / "snapshots").exists()
    with pytest.raises(KeyError):
        module.operation()
    snapshot = load_one(tmp_path / "snapshots")
    assert snapshot.complete
    assert len(snapshot.data["interactions"]) == 1
    monkeypatch.setenv("DECORATOR_FORBID_PROVIDER", "1")
    report = module.operation.rewind.replay(snapshots(tmp_path / "snapshots")[0])
    assert report.status == "reproduced", report
    assert report.consumed == 1


def test_custom_dictionary_descriptor_is_not_evaluated(tmp_path):
    calls = []

    @capture(store=tmp_path / "snapshots")
    class Custom:
        @property
        def __dict__(self):
            calls.append("side effect")
            return {}

        def calculate(self):
            return 8

    assert Custom().calculate() == 8
    assert calls == []
    assert not load_one(tmp_path / "snapshots").complete


def test_class_double_decoration_is_rejected_without_partial_mutation(tmp_path):
    class Service:
        def first(self):
            return 1

        @capture(store=tmp_path / "snapshots")
        def second(self):
            return 2

    first = Service.first
    with pytest.raises(TypeError):
        capture(Service, store=tmp_path / "snapshots")
    assert Service.first is first


def test_class_method_new_shared_state_is_ineligible(tmp_path):
    @capture(store=tmp_path / "snapshots")
    class Counter:
        @classmethod
        def increment(cls):
            cls.count = getattr(cls, "count", 0) + 1
            return cls.count

    assert Counter.increment() == 1
    snapshot = load_one(tmp_path / "snapshots")
    assert not snapshot.complete
    assert "class_state_unsupported" in snapshot.data["capture"]["ineligible_reasons"]


def test_inherited_class_method_receiver_is_not_silently_replaced(tmp_path):
    @capture(store=tmp_path / "snapshots")
    class Base:
        @classmethod
        def name(cls):
            return cls.__name__

    class Child(Base):
        pass

    assert Child.name() == "Child"
    snapshot = load_one(tmp_path / "snapshots")
    assert not snapshot.complete
    assert "receiver_class_unsupported" in snapshot.data["capture"]["ineligible_reasons"]
