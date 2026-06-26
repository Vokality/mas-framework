from __future__ import annotations

from mas_agent._core import EarlyReplies
from mas_core import EnvelopeMessage, MessageMeta


def _reply(message_id: str, correlation_id: str) -> EnvelopeMessage:
    return EnvelopeMessage(
        message_id=message_id,
        sender_id="target",
        target_id="sender",
        message_type="reply",
        data={},
        meta=MessageMeta(is_reply=True, correlation_id=correlation_id),
    )


def test_early_replies_expire_by_ttl() -> None:
    replies = EarlyReplies(max_entries=10, ttl_seconds=1.0)
    replies.put("corr-1", _reply("reply-1", "corr-1"), now=10.0)

    reply = replies.pop("corr-1", now=10.5)
    assert reply is not None
    assert reply.message_id == "reply-1"

    replies.put("corr-2", _reply("reply-2", "corr-2"), now=10.0)
    assert replies.pop("corr-2", now=11.1) is None


def test_claim_returns_aged_reply_with_expire_false() -> None:
    # A reply buffered before its request registers must still be claimable even
    # if the Request round-trip outlived the TTL: the owner is awaiting it.
    replies = EarlyReplies(max_entries=10, ttl_seconds=1.0)
    replies.put("corr-1", _reply("reply-1", "corr-1"), now=10.0)

    # Default expiry would drop it...
    assert replies.pop("corr-1", now=100.0) is None
    # ...but the owning request claims it regardless of age.
    replies.put("corr-1", _reply("reply-1", "corr-1"), now=10.0)
    claimed = replies.pop("corr-1", now=100.0, expire=False)
    assert claimed is not None
    assert claimed.message_id == "reply-1"


def test_early_replies_are_bounded() -> None:
    replies = EarlyReplies(max_entries=2, ttl_seconds=60.0)
    replies.put("corr-1", _reply("reply-1", "corr-1"), now=1.0)
    replies.put("corr-2", _reply("reply-2", "corr-2"), now=2.0)
    replies.put("corr-3", _reply("reply-3", "corr-3"), now=3.0)

    assert len(replies) == 2
    assert replies.pop("corr-1", now=3.0) is None
    reply_2 = replies.pop("corr-2", now=3.0)
    reply_3 = replies.pop("corr-3", now=3.0)
    assert reply_2 is not None
    assert reply_3 is not None
    assert reply_2.message_id == "reply-2"
    assert reply_3.message_id == "reply-3"
