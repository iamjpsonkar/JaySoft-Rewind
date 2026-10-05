import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

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
