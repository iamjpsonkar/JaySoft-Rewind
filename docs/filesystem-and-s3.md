# Filesystem and S3 observations

[Documentation home](index.md)

These explicit wrappers cover selected calls. They do not globally intercept
filesystem access or all AWS SDK methods. Replay never invokes the wrapped live
operation. Inputs and results pass through bounded capture policy; transformed
or unsupported observations make strict replay ineligible.

## Filesystem

```python
from rewind.adapters.filesystem import RecordingFilesystem

files = RecordingFilesystem("/path/to/fixture-directory", dependency="templates")
# Run these calls inside rewind.run_sync / rewind.run.
text = files.read_text("template.txt")
```

Supported methods: `read_bytes`, `read_text`, `write_bytes`, `write_text`, `exists`,
`listdir`, and `unlink`. Only relative paths without `..` are accepted. Existing
symlink components are rejected. Replay does not inspect the root or perform
writes. This is not containment against another process replacing path components.
The root itself is developer-selected and must be trusted.

Missing-file and other OS errors keep their original behavior during capture.
Exceptions containing filename attributes currently make replay ineligible,
because the adapter does not retain absolute host paths. File content uses the
value capture policy; enable it only for permitted data. No descriptors, file
locks, permissions, mmap, filesystem watchers, or global `open` patching are supplied.

## S3

```bash
pip install 'jaysoft-rewind[s3]'
```

```python
import boto3
from rewind.adapters.s3 import RecordingS3

s3 = RecordingS3(boto3.client("s3"), dependency="documents")
# Inside a capture scope:
response = s3.get_object(Bucket="fixture-bucket", Key="document.txt")
with response["Body"] as body:
    contents = body.read()
```

Supported calls: `put_object`, `get_object`, `head_object`, `delete_object`, and
`list_objects_v2`. Body `read(amt)` and `close()` remain lazy and ordered. Replay
may construct `RecordingS3()` with no live client. Uploads must use bounded bytes
or strings for complete capture; multipart transfers, upload file objects,
paginators, resources and unsupported body methods are outside this contract.

Capture returns original SDK timestamps. Replay preserves their instant and UTC
offset as standard fixed-offset `datetime` objects; timezone implementation
identity is not preserved. Convert SDK timestamps to an application DTO (for
example `isoformat()`) before returning the entry point result. Modeled S3 errors
are recreated from the locally installed SDK's fixed S3 exception definitions.
Unknown transport exceptions make a recording ineligible.

Client credentials/configuration are never copied. Customer encryption keys are
excluded and make the recording ineligible. Object data, names, metadata, and
error messages can still be sensitive: use a deliberate capture policy and
private artifact storage. Tests use the actual boto3 library with Moto's S3
emulation; they are not evidence of validation against an AWS account.
