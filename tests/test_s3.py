from datetime import datetime
from io import BytesIO

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from dateutil.tz import tzutc
from moto import mock_aws

from rewind import Limits
from rewind.adapters.s3 import RecordingS3


@mock_aws
def test_real_boto3_moto_replay_is_offline(recorder):
    raw = boto3.client("s3", region_name="us-east-1")
    raw.create_bucket(Bucket="rewind-fixture")
    s3 = RecordingS3(raw)

    def operation():
        result = s3.put_object(Bucket="rewind-fixture", Key="sample", Body=b"fixture")
        response = s3.get_object(Bucket="rewind-fixture", Key="sample")
        with response["Body"] as stream:
            content = stream.read(3) + stream.read()
        head = s3.head_object(Bucket="rewind-fixture", Key="sample")
        listing = s3.list_objects_v2(Bucket="rewind-fixture")
        s3.delete_object(Bucket="rewind-fixture", Key="sample")
        return result, content, head["LastModified"].isoformat(), listing["KeyCount"]

    result = recorder.run_sync(operation)
    assert result[1] == b"fixture"
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.complete, snapshot.data["capture"]
    s3.client = None
    assert recorder.replay_sync(snapshot, operation).reproduced


@mock_aws
def test_s3_client_error_preserved(recorder):
    s3 = RecordingS3(boto3.client("s3", region_name="us-east-1"))

    def operation():
        try:
            s3.head_object(Bucket="missing-bucket", Key="missing")
        except ClientError as exc:
            return exc.response, exc.operation_name

    recorder.run_sync(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.complete
    s3.client = None
    assert recorder.replay_sync(snapshot, operation).reproduced


def test_s3_does_not_capture_customer_keys(recorder):
    class Client:
        def put_object(self, **kwargs):
            return {"ok": True}

    s3 = RecordingS3(Client())
    recorder.run_sync(
        lambda: s3.put_object(Bucket="a", Key="b", Body=b"x", SSECustomerKey="never-save-this")
    )
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert not snapshot.complete
    assert b"never-save-this" not in snapshot.raw


@mock_aws
def test_s3_changed_parameters_never_fall_back(recorder):
    raw = boto3.client("s3", region_name="us-east-1")
    raw.create_bucket(Bucket="rewind-fixture")
    s3 = RecordingS3(raw)
    key = "one"

    def operation():
        return s3.put_object(Bucket="rewind-fixture", Key=key, Body=b"x")

    recorder.run_sync(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    key = "two"
    s3.client = None
    assert recorder.replay_sync(snapshot, operation).status == "diverged"


def test_s3_unsupported_methods_explicit():
    with pytest.raises(AttributeError):
        _ = RecordingS3().upload_file


class StreamingClient:
    def __init__(self):
        self.responses = []

    def get_object(self, **kwargs):
        data = b"first" if not self.responses else b"other"
        response = {"Body": StreamingBody(BytesIO(data), len(data)), "ContentLength": len(data)}
        self.responses.append(response)
        return response


@pytest.mark.parametrize("method", ["read", "close"])
@pytest.mark.parametrize("separate_wrappers", [False, True])
def test_identical_gets_preserve_distinct_body_identity(recorder, method, separate_wrappers):
    client = StreamingClient()
    first_client = RecordingS3(client)
    second_client = RecordingS3(client) if separate_wrappers else first_client
    changed = False

    def operation():
        first = first_client.get_object(Bucket="fixture", Key="same")["Body"]
        second = second_client.get_object(Bucket="fixture", Key="same")["Body"]
        selected = second if changed else first
        return getattr(selected, method)()

    expected = recorder.run_sync(operation)
    assert expected == (b"first" if method == "read" else None)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.complete
    first_client.client = second_client.client = None
    assert recorder.replay_sync(snapshot, operation).reproduced
    changed = True
    report = recorder.replay_sync(snapshot, operation)
    assert report.status == "diverged" and report.consumed == 2
    assert len(client.responses) == 2


@pytest.mark.parametrize("foreign_capture", [False, True])
def test_precapture_and_foreign_body_reads_are_ineligible(recorder, foreign_capture):
    client = StreamingClient()
    s3 = RecordingS3(client)
    handles = []

    def acquire():
        handles.append(s3.get_object(Bucket="fixture", Key="same")["Body"])

    if foreign_capture:
        recorder.run_sync(acquire)
    else:
        acquire()
    previous = set(recorder.store.ids())
    assert recorder.run_sync(lambda: handles[0].read()) == b"first"
    identifier = (set(recorder.store.ids()) - previous).pop()
    snapshot = recorder.store.load(identifier)
    assert not snapshot.complete
    assert "s3_body_scope_unsupported" in snapshot.data["capture"]["ineligible_reasons"]


@pytest.mark.parametrize("shape", ["wide", "deep"])
def test_response_normalization_limits_preserve_live_result(recorder, shape):
    recorder.limits = Limits(items=64, depth=8)
    if shape == "wide":
        contents = list(range(1000))
    else:
        contents = []
        for _ in range(20):
            contents = [contents]
    response = {"Contents": contents}

    class Client:
        def list_objects_v2(self, **kwargs):
            return response

    s3 = RecordingS3(Client())

    def operation():
        result = s3.list_objects_v2(Bucket="fixture")
        assert result is response and result["Contents"] is contents
        return "unchanged"

    assert recorder.run_sync(operation) == "unchanged"
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert not snapshot.complete
    assert "s3_normalization_limit" in snapshot.data["capture"]["ineligible_reasons"]


def test_normalization_stops_before_visiting_values_beyond_budget(recorder):
    recorder.limits = Limits(items=64)

    class TrapTimezone(tzutc):
        def utcoffset(self, value):
            pytest.fail("normalization visited data beyond its configured item budget")

    response = {"Contents": [None] * 1000 + [datetime(2020, 1, 1, tzinfo=TrapTimezone())]}

    class Client:
        def list_objects_v2(self, **kwargs):
            return response

    def operation():
        assert RecordingS3(Client()).list_objects_v2(Bucket="fixture") is response

    recorder.run_sync(operation)
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert "s3_normalization_limit" in snapshot.data["capture"]["ineligible_reasons"]


def test_sdk_timestamps_preserve_live_identity_and_replay_instant(recorder):
    timestamp = datetime(2020, 1, 1, tzinfo=tzutc())
    response = {"LastModified": timestamp}

    class Client:
        def head_object(self, **kwargs):
            return response

    s3 = RecordingS3(Client())
    replaying = False

    def operation():
        result = s3.head_object(Bucket="fixture", Key="same")
        if not replaying:
            assert result is response and result["LastModified"] is timestamp
        return result["LastModified"].isoformat()

    assert recorder.run_sync(operation) == timestamp.isoformat()
    snapshot = recorder.store.load(recorder.store.ids()[0])
    assert snapshot.complete
    replaying = True
    s3.client = None
    assert recorder.replay_sync(snapshot, operation).reproduced
