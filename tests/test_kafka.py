"""Kafka boundary contracts plus opt-in real broker conformance."""

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import kafka
import pytest
from kafka.consumer.fetcher import ConsumerRecord
from kafka.producer.future import FutureProduceResult, FutureRecordMetadata, RecordMetadata
from kafka.structs import TopicPartition

from rewind import CapturePolicy, Limits
from rewind.adapters.kafka import RecordingKafkaConsumer, RecordingKafkaProducer


def saved(recorder):
    ids = recorder.store.ids()
    assert len(ids) == 1
    return recorder.store.load(ids[0])


def future(*, completed=True):
    produced = FutureProduceResult(TopicPartition("fixture", 0))
    result = FutureRecordMetadata(produced, 0, 123, None, 1, 5, 0)
    if completed:
        produced.success((7, -1, None))
    return result


class Producer:
    def __init__(self, result=None):
        self.result = result if result is not None else future()
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(("send", kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def flush(self, timeout=None):
        self.calls.append(("flush", timeout))

    def close(self, timeout=None):
        self.calls.append(("close", timeout))


def record(value=b"value", headers=None):
    fields = dict(topic="fixture", partition=0, leader_epoch=0, offset=7, timestamp=123,
                  timestamp_type=0, key=b"k", value=value, headers=headers or [], checksum=None,
                  serialized_key_size=1, serialized_value_size=5, serialized_header_size=0)
    return ConsumerRecord(**{name: fields[name] for name in ConsumerRecord._fields})


class Consumer:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def poll(self, **kwargs):
        self.calls.append(("poll", kwargs))
        return {TopicPartition("fixture", 0): self.records}

    def close(self, autocommit=True):
        self.calls.append(("close", autocommit))


async def test_send_future_metadata_and_lifecycle_without_live_replay(recorder):
    inner = Producer()
    producer = RecordingKafkaProducer(inner)

    async def operation():
        pending = producer.send("fixture", b"value", key=b"k", headers=[("trace", b"demo")])
        result = pending.get(timeout=1)
        assert type(result) is RecordMetadata
        assert type(result.topic_partition) is TopicPartition
        producer.flush(timeout=1)
        producer.close(timeout=1)
        return result.topic, result.offset, result.timestamp, result.serialized_value_size

    assert await recorder.run(operation) == ("fixture", 7, 123, 5)
    snapshot = saved(recorder)
    assert snapshot.complete
    assert [item["operation"] for item in snapshot.data["interactions"]] == ["messaging.call"] * 4
    producer.inner = None
    assert (await recorder.replay(snapshot, operation)).reproduced
    assert len(inner.calls) == 3


async def test_send_preserves_future_laziness_and_timeout(recorder):
    native_future = future(completed=False)
    producer = RecordingKafkaProducer(Producer(native_future))

    async def operation():
        pending = producer.send("fixture", b"value")
        assert not native_future.is_done
        try:
            pending.get(timeout=0)
        except kafka.errors.KafkaTimeoutError as exc:
            return exc.args
        raise AssertionError("future should not be completed")

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete
    producer.inner = None
    assert (await recorder.replay(snapshot, operation)).reproduced


@pytest.mark.parametrize("value", [b"value", "decoded", {"item": [1, b"two"]}, None])
async def test_poll_preserves_partition_and_record_types(recorder, value):
    inner = Consumer([record(value)])
    consumer = RecordingKafkaConsumer(inner)

    async def operation():
        batch = consumer.poll(timeout_ms=10, max_records=1, update_offsets=False)
        partition, records = next(iter(batch.items()))
        assert type(partition) is TopicPartition
        assert type(records[0]) is ConsumerRecord
        assert records[0].value == value
        consumer.close(autocommit=False)
        return records[0].offset

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert snapshot.complete
    consumer.inner = None
    assert (await recorder.replay(snapshot, operation)).reproduced
    assert len(inner.calls) == 2


@pytest.mark.parametrize("changes", ["topic", "payload", "headers", "timeout", "dependency"])
async def test_changed_submission_or_wait_diverges(recorder, changes):
    inner = Producer()
    producer = RecordingKafkaProducer(inner)
    changed = False

    async def operation():
        result = producer.send(
            "other" if changed and changes == "topic" else "fixture",
            b"other" if changed and changes == "payload" else b"value",
            headers=[("trace", b"other" if changed and changes == "headers" else b"demo")],
        ).get(timeout=2 if changed and changes == "timeout" else 1)
        return result.offset

    await recorder.run(operation)
    changed = True
    if changes == "dependency":
        producer.dependency = "other"
    assert (await recorder.replay(saved(recorder), operation)).status == "diverged"
    assert len(inner.calls) == 1


@pytest.mark.parametrize("direction", ["send", "poll"])
@pytest.mark.parametrize("sensitive", ["header", "json", "custom"])
async def test_sensitive_messages_removed_before_capture(recorder, direction, sensitive):
    recorder.policy = replace(CapturePolicy.synthetic(), redacted_keys=("customer_id",))
    value = (
        b'{"password":"PRIVATE_MESSAGE"}' if sensitive == "json"
        else {"customer_id": "PRIVATE_MESSAGE"}
    )
    header = [("Authorization", b"PRIVATE_HEADER")] if sensitive == "header" else []
    producer = RecordingKafkaProducer(Producer())
    consumer = RecordingKafkaConsumer(Consumer([record(value, header)]))

    async def operation():
        if direction == "send":
            producer.send("fixture", value, headers=header)
        else:
            consumer.poll()

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"PRIVATE_MESSAGE" not in snapshot.raw and b"PRIVATE_HEADER" not in snapshot.raw


async def test_default_policy_excludes_payloads(recorder):
    recorder.policy = CapturePolicy()
    producer = RecordingKafkaProducer(Producer())

    async def operation():
        producer.send("fixture", b"PRIVATE_MESSAGE", key=b"PRIVATE_KEY")

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"PRIVATE" not in snapshot.raw


async def test_handles_from_other_capture_are_ineligible(recorder):
    producer = RecordingKafkaProducer(Producer())
    pending = producer.send("fixture", b"value")

    async def operation():
        return pending.get(timeout=1).offset

    assert await recorder.run(operation) == 7
    assert not saved(recorder).complete


async def test_child_task_future_access_marks_capture_ineligible(recorder):
    producer = RecordingKafkaProducer(Producer())

    async def operation():
        pending = producer.send("fixture", b"value")

        async def child():
            return pending.get(timeout=1).offset

        return await asyncio.create_task(child())

    assert await recorder.run(operation) == 7
    assert "child_task_unsupported" in saved(recorder).data["capture"]["ineligible_reasons"]


async def test_capture_limits_do_not_change_live_send(recorder):
    recorder.limits = Limits(body_bytes=4)
    inner = Producer()
    producer = RecordingKafkaProducer(inner)

    async def operation():
        return producer.send("fixture", b"large payload").get(timeout=1).offset

    assert await recorder.run(operation) == 7
    assert inner.calls[0][1]["value"] == b"large payload"
    assert not saved(recorder).complete


async def test_copied_thread_future_access_marks_capture_ineligible(recorder):
    producer = RecordingKafkaProducer(Producer())

    async def operation():
        pending = producer.send("fixture", b"value")
        return await asyncio.to_thread(lambda: pending.get(timeout=1).offset)

    assert await recorder.run(operation) == 7
    assert not saved(recorder).complete


async def test_unknown_exception_preserves_live_identity_without_recording_message(recorder):
    class UnsupportedError(Exception):
        pass

    error = UnsupportedError("PRIVATE_EXCEPTION")
    producer = RecordingKafkaProducer(Producer(error))

    async def operation():
        with pytest.raises(UnsupportedError) as caught:
            producer.send("fixture", b"value")
        assert caught.value is error

    await recorder.run(operation)
    snapshot = saved(recorder)
    assert not snapshot.complete
    assert b"PRIVATE_EXCEPTION" not in snapshot.raw


@pytest.mark.parametrize(
    "failure", [kafka.errors.NoBrokersAvailable, kafka.errors.KafkaTimeoutError]
)
async def test_send_errors_preserve_allowed_type_and_arguments(recorder, failure):
    producer = RecordingKafkaProducer(Producer(failure("fixture error")))

    async def operation():
        try:
            producer.send("fixture", b"value")
        except failure as exc:
            return exc.args

    await recorder.run(operation)
    producer.inner = None
    assert (await recorder.replay(saved(recorder), operation)).reproduced


async def test_real_kafka_broker_conformance(recorder):
    broker = os.environ.get("REWIND_KAFKA_BOOTSTRAP")
    if not broker:
        pytest.skip("set REWIND_KAFKA_BOOTSTRAP to a disposable Kafka broker")
    topic = "rewind-fixture-" + uuid4().hex
    producer_inner = kafka.KafkaProducer(bootstrap_servers=[broker], max_block_ms=15000)
    consumer_inner = kafka.KafkaConsumer(
        topic, bootstrap_servers=[broker], group_id=None, enable_auto_commit=False,
        auto_offset_reset="earliest", request_timeout_ms=10000,
    )
    producer = RecordingKafkaProducer(producer_inner)
    consumer = RecordingKafkaConsumer(consumer_inner)

    async def operation():
        metadata = producer.send(
            topic, b"fixture", key=b"key", headers=[("trace", b"demo")]
        ).get(10)
        received = None
        for _ in range(20):
            batch = consumer.poll(timeout_ms=500, max_records=1)
            if batch:
                received = next(iter(batch.values()))[0]
                break
        assert received is not None
        assert type(metadata) is RecordMetadata and type(received) is ConsumerRecord
        assert received.value == b"fixture" and received.key == b"key"
        assert received.headers == [("trace", b"demo")]
        assert metadata.topic == received.topic and metadata.partition == received.partition
        assert metadata.offset == received.offset
        return metadata.offset, received.timestamp

    try:
        await recorder.run(operation)
        snapshot = saved(recorder)
        assert snapshot.complete, snapshot.data["capture"]
        producer.inner = consumer.inner = None
        assert (await recorder.replay(snapshot, operation)).reproduced
    finally:
        producer_inner.close(timeout=5)
        consumer_inner.close(autocommit=False)
