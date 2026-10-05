"""Synthetic source fixture used to record the published 0.1.0a2 golden artifact."""

from datetime import UTC, date, datetime
from uuid import UUID

from rewind import CapturePolicy, Retention, Rewind


def factory(store=None):
    recorder = Rewind(
        application="golden-contract",
        code_paths=[__file__],
        store=store,
        policy=CapturePolicy.synthetic(),
        retain=Retention(always=True),
    )

    async def entrypoint(payload):
        observed = recorder.value(
            "fixture.value",
            lambda: {
                "day": date(2024, 1, 2),
                "moment": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
                "identifier": UUID("12345678-1234-5678-1234-567812345678"),
                "binary": b"synthetic fixture",
                "tuple": (7, None, True),
            },
        )
        return {"input": payload, "observed": observed}

    return recorder, entrypoint
