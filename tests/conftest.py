import pytest

from rewind import CapturePolicy, LocalStore, Retention, Rewind


@pytest.fixture
def recorder(tmp_path):
    return Rewind(
        application="tests",
        code_paths=[__file__],
        store=LocalStore(tmp_path / "snapshots"),
        policy=CapturePolicy.synthetic(),
        retain=Retention(always=True),
    )
