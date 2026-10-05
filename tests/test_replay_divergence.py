"""Internal divergence exceptions must never be mistaken for successful returns."""

import httpx
import pytest

from rewind.comparison import compare_file
from rewind.errors import ReplayDivergence


def saved(recorder):
    return recorder.store.load(recorder.store.ids()[0])


async def test_direct_divergence_is_not_a_successful_async_none_return(recorder):
    changed = False

    async def operation():
        if changed:
            raise ReplayDivergence("private application message")

    assert await recorder.run(operation) is None
    changed = True
    report = await recorder.replay(saved(recorder), operation)
    assert report.status == "diverged"
    assert report.detail == "application raised replay divergence"
    assert "private" not in report.detail


def test_direct_divergence_is_not_a_successful_sync_none_return(recorder):
    changed = False

    def operation():
        if changed:
            raise ReplayDivergence("private application message")

    assert recorder.run_sync(operation) is None
    changed = True
    report = recorder.replay_sync(saved(recorder), operation)
    assert report.status == "diverged"
    assert report.detail == "application raised replay divergence"


async def test_direct_divergence_after_complete_asgi_response_is_not_ignored(recorder):
    changed = False

    async def app(scope, receive, send):
        while (await receive()).get("more_body", False):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
        if changed:
            raise ReplayDivergence("private post-response error")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=recorder.asgi(app)), base_url="http://test"
    ) as client:
        assert (await client.get("/")).content == b"ok"
    snapshot = saved(recorder)
    assert snapshot.complete
    changed = True
    report = await recorder.replay_asgi(snapshot, app)
    assert report.status == "diverged"
    assert report.detail == "application raised replay divergence"


@pytest.mark.parametrize("synchronous", [False, True])
async def test_original_adapter_divergence_remains_the_first_failure(recorder, synchronous):
    changed = False

    def operation():
        recorder.sources.randint(1, 2 if changed else 1)

    async def asynchronous():
        operation()

    if synchronous:
        recorder.run_sync(operation)
    else:
        await recorder.run(asynchronous)
    changed = True
    report = (
        recorder.replay_sync(saved(recorder), operation)
        if synchronous
        else await recorder.replay(saved(recorder), asynchronous)
    )
    assert report.status == "diverged"
    assert "interaction 1" in report.detail
    assert "application raised" not in report.detail


async def test_changed_code_cannot_match_none_oracle_by_raising_divergence(tmp_path, monkeypatch):
    from tests.test_comparison import SOURCE, fixture

    source, artifact = await fixture(tmp_path, monkeypatch, "return None")
    source.write_text(
        "from rewind.errors import ReplayDivergence\n"
        + SOURCE.replace("BODY", "raise ReplayDivergence('private application message')")
    )
    report = compare_file(artifact, "comparison_fixture:factory", expected_return=None)
    assert not report.matched
    assert report.status == "diverged"
    assert report.detail == "application raised replay divergence"
