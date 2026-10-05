"""Explicit kafka-python producer futures and consumer poll observations."""

from typing import Any

from kafka import errors  # type: ignore[import-untyped]
from kafka.consumer.fetcher import ConsumerRecord  # type: ignore[import-untyped]
from kafka.producer.future import RecordMetadata  # type: ignore[import-untyped]
from kafka.structs import TopicPartition  # type: ignore[import-untyped]

from ..recorder import Recorder
from ..replay import ReplaySession
from ._messaging import OwnedHandle, current, headers, message, observe, unsupported

_ERROR_CLASSES = (
    errors.KafkaTimeoutError, errors.KafkaConnectionError, errors.NoBrokersAvailable,
    errors.UnknownTopicOrPartitionError, errors.RequestTimedOutError,
    errors.MessageSizeTooLargeError, errors.KafkaConfigurationError,
    ValueError, TypeError, AssertionError,
)
_ERRORS = {f"{cls.__module__}.{cls.__qualname__}": cls for cls in _ERROR_CLASSES}


def _partition(data: Any, active: ReplaySession) -> TopicPartition:
    if (type(data) is not dict or set(data) != {"topic", "partition"}
            or type(data["topic"]) is not str or type(data["partition"]) is not int):
        active.fail("invalid recorded Kafka partition")
    return TopicPartition(data["topic"], data["partition"])


def _prepare(data: Any, active: Recorder) -> Any:
    result = dict(data)
    for key in ("key", "value"):
        if key in result:
            result[key] = message(result[key], active)
    if "headers" in result:
        result["headers"] = headers(result["headers"], active)
    return result


def _metadata(value: Any, active: Recorder) -> dict[str, Any]:
    if type(value) is not RecordMetadata:
        raise ValueError("unsupported Kafka metadata type")
    data = value._asdict()
    data["topic_partition"] = dict(value.topic_partition._asdict())
    return data


def _restore_metadata(data: Any, active: ReplaySession) -> RecordMetadata:
    if type(data) is not dict or set(data) != set(RecordMetadata._fields):
        active.fail("invalid recorded Kafka metadata")
    if type(data["topic"]) is not str or type(data["partition"]) is not int:
        active.fail("invalid recorded Kafka metadata identity")
    fields = dict(data)
    fields["topic_partition"] = _partition(fields["topic_partition"], active)
    return RecordMetadata(**fields)


def _records(value: Any, active: Recorder) -> list[dict[str, Any]]:
    if type(value) is not dict or len(value) > active.limits.items:
        raise ValueError("unsupported Kafka poll result")
    result = []
    count = 0
    for partition, records in value.items():
        if type(partition) is not TopicPartition or type(records) is not list:
            raise ValueError("unsupported Kafka poll partition")
        count += len(records)
        if count > active.limits.items:
            raise ValueError("Kafka record count limit")
        captured = []
        for record in records:
            if type(record) is not ConsumerRecord:
                raise ValueError("unsupported Kafka consumer record")
            captured.append(_prepare(record._asdict(), active))
        result.append({"partition": dict(partition._asdict()), "records": captured})
    return result


def _restore_records(
    data: Any, active: ReplaySession,
) -> dict[TopicPartition, list[ConsumerRecord]]:
    if type(data) is not list:
        active.fail("invalid recorded Kafka poll result")
    result = {}
    for group in data:
        if type(group) is not dict or set(group) != {"partition", "records"}:
            active.fail("invalid recorded Kafka partition group")
        partition = _partition(group["partition"], active)
        if partition in result or type(group["records"]) is not list:
            active.fail("duplicate or invalid Kafka poll partition")
        records = []
        for item in group["records"]:
            if type(item) is not dict or set(item) != set(ConsumerRecord._fields):
                active.fail("invalid recorded Kafka consumer record")
            if item["topic"] != partition.topic or item["partition"] != partition.partition:
                active.fail("Kafka record partition differs from group")
            records.append(ConsumerRecord(**item))
        result[partition] = records
    return result


class RecordingKafkaFuture(OwnedHandle):
    def __init__(self, inner: Any, owner: Any, target: str, dependency: str) -> None:
        super().__init__(inner, owner, target)
        self._dependency = dependency

    def get(self, timeout: float | None = None) -> RecordMetadata:
        self._check()
        return observe(
            "kafka", self._dependency, self._target, "get", {"timeout": timeout},
            lambda: self._inner.get(timeout=timeout), allowed=_ERRORS,
            pack=_metadata, unpack=_restore_metadata,
        )


class RecordingKafkaProducer:
    """Wrap a configured KafkaProducer; no client is needed during replay."""

    def __init__(self, inner: Any = None, *, dependency: str = "kafka.producer") -> None:
        self.inner = inner
        self.dependency = dependency
        self._handles = 0

    def send(
        self, topic: str, value: Any = None, key: Any = None, headers: Any = None,
        partition: int | None = None, timestamp_ms: int | None = None,
    ) -> RecordingKafkaFuture:
        owner = current()
        parameters = {"topic": topic, "value": value, "key": key, "headers": headers,
                      "partition": partition, "timestamp_ms": timestamp_ms}

        def live() -> RecordingKafkaFuture:
            future = self.inner.send(**parameters)
            self._handles += 1
            return RecordingKafkaFuture(
                future, owner, f"producer.future:{self._handles}", self.dependency
            )

        def restore(data: Any, active: ReplaySession) -> RecordingKafkaFuture:
            if (type(data) is not dict or set(data) != {"handle"}
                    or type(data["handle"]) is not str
                    or not data["handle"].startswith("producer.future:")):
                active.fail("invalid recorded Kafka future")
            return RecordingKafkaFuture(None, active, data["handle"], self.dependency)

        return observe(
            "kafka", self.dependency, "producer", "send", parameters, live,
            allowed=_ERRORS, prepare=_prepare,
            pack=lambda result, active: {"handle": result._target}, unpack=restore,
        )

    def flush(self, timeout: float | None = None) -> None:
        return observe(
            "kafka", self.dependency, "producer", "flush", {"timeout": timeout},
            lambda: self.inner.flush(timeout=timeout), allowed=_ERRORS,
        )

    def close(self, timeout: float | None = None) -> None:
        return observe(
            "kafka", self.dependency, "producer", "close", {"timeout": timeout},
            lambda: self.inner.close(timeout=timeout), allowed=_ERRORS,
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        unsupported("kafka_producer_operation_unsupported")
        return getattr(self.inner, name)


class RecordingKafkaConsumer:
    """Wrap an already configured consumer; poll is the application boundary."""

    def __init__(self, inner: Any = None, *, dependency: str = "kafka.consumer") -> None:
        self.inner = inner
        self.dependency = dependency

    def poll(
        self, timeout_ms: int = 0, max_records: int | None = None, update_offsets: bool = True,
    ) -> dict[TopicPartition, list[ConsumerRecord]]:
        parameters = {"timeout_ms": timeout_ms, "max_records": max_records,
                      "update_offsets": update_offsets}
        return observe(
            "kafka", self.dependency, "consumer", "poll", parameters,
            lambda: self.inner.poll(**parameters), allowed=_ERRORS,
            pack=_records, unpack=_restore_records,
        )

    def close(self, autocommit: bool = True) -> None:
        return observe(
            "kafka", self.dependency, "consumer", "close", {"autocommit": autocommit},
            lambda: self.inner.close(autocommit=autocommit), allowed=_ERRORS,
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        unsupported("kafka_consumer_operation_unsupported")
        return getattr(self.inner, name)
