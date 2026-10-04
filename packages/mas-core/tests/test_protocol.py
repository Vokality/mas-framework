"""Default message identity remains independent of wall-clock uniqueness."""

from __future__ import annotations

import time
from uuid import RFC_4122, UUID

import pytest
from mas_core.protocol import EnvelopeMessage


def test_repeated_wall_clock_still_produces_distinct_uuid4_message_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "time_ns", lambda: 1_000_000_000)
    first = EnvelopeMessage(
        sender_id="sender", target_id="target", message_type="work", data={"value": 1}
    )
    second = EnvelopeMessage(
        sender_id="sender", target_id="target", message_type="work", data={"value": 2}
    )
    assert first.message_id != second.message_id
    for message in (first, second):
        identity = UUID(message.message_id)
        assert identity.version == 4
        assert identity.variant == RFC_4122


def test_explicit_message_id_and_timestamp_are_preserved() -> None:
    message = EnvelopeMessage(
        sender_id="sender",
        target_id="target",
        message_type="work",
        data={},
        message_id="external-id-123",
        timestamp=123.25,
    )
    assert message.message_id == "external-id-123"
    assert message.timestamp == 123.25
    assert EnvelopeMessage.model_validate_json(message.model_dump_json()) == message


def test_default_timestamp_retains_wall_clock_seconds() -> None:
    before = time.time()
    message = EnvelopeMessage(
        sender_id="sender", target_id="target", message_type="work", data={}
    )
    after = time.time()
    assert before <= message.timestamp <= after
