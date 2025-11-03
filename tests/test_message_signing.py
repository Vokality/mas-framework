"""Tests for Message Signing Module."""

import time

import pytest
from redis.asyncio import Redis

from mas.gateway.message_signing import MessageSigningModule

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def redis():
    """Create Redis connection."""
    r = Redis.from_url("redis://localhost:6379", decode_responses=True)

    # Cleanup before tests
    await r.flushdb()

    yield r

    # Cleanup after tests
    await r.flushdb()
    await r.aclose()


@pytest.fixture
async def signing_module(redis):
    """Create MessageSigningModule."""
    return MessageSigningModule(redis)


class TestKeyManagement:
    """Test signing key management."""

    async def test_generate_signing_key(self, signing_module):
        """Test generating a signing key."""
        key = await signing_module.generate_signing_key("agent-1")

        # Key should be 64 hex characters (32 bytes)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    async def test_get_signing_key(self, signing_module):
        """Test retrieving a signing key."""
        original_key = await signing_module.generate_signing_key("agent-1")
        retrieved_key = await signing_module.get_signing_key("agent-1")

        assert retrieved_key == original_key

    async def test_get_nonexistent_key(self, signing_module):
        """Test retrieving a nonexistent key."""
        key = await signing_module.get_signing_key("nonexistent")
        assert key is None

    async def test_rotate_signing_key(self, signing_module):
        """Test rotating a signing key."""
        old_key = await signing_module.generate_signing_key("agent-1")
        new_key = await signing_module.rotate_signing_key("agent-1")

        # Keys should be different
        assert new_key != old_key

        # New key should be current
        current_key = await signing_module.get_signing_key("agent-1")
        assert current_key == new_key

    async def test_delete_signing_key(self, signing_module):
        """Test deleting a signing key."""
        await signing_module.generate_signing_key("agent-1")
        await signing_module.delete_signing_key("agent-1")

        key = await signing_module.get_signing_key("agent-1")
        assert key is None


class TestMessageSigning:
    """Test message signing functionality."""

    async def test_sign_message(self, signing_module):
        """Test signing a message."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test", "data": "hello"}
        result = await signing_module.sign_message("agent-1", "msg-123", payload)

        # Should return signature, timestamp, and nonce
        assert "signature" in result
        assert "timestamp" in result
        assert "nonce" in result

        # Signature should be hex string (HMAC-SHA256 = 64 hex chars)
        assert len(result["signature"]) == 64
        assert isinstance(result["timestamp"], float)
        assert isinstance(result["nonce"], str)

    async def test_sign_message_no_key(self, signing_module):
        """Test signing without a key raises error."""
        payload = {"action": "test"}

        with pytest.raises(ValueError, match="No signing key found"):
            await signing_module.sign_message("agent-1", "msg-123", payload)

    async def test_sign_message_custom_timestamp(self, signing_module):
        """Test signing with custom timestamp."""
        await signing_module.generate_signing_key("agent-1")

        custom_time = time.time() - 60  # 1 minute ago
        payload = {"action": "test"}

        result = await signing_module.sign_message(
            "agent-1", "msg-123", payload, timestamp=custom_time
        )

        assert result["timestamp"] == custom_time

    async def test_sign_message_custom_nonce(self, signing_module):
        """Test signing with custom nonce."""
        await signing_module.generate_signing_key("agent-1")

        custom_nonce = "my-custom-nonce"
        payload = {"action": "test"}

        result = await signing_module.sign_message(
            "agent-1", "msg-123", payload, nonce=custom_nonce
        )

        assert result["nonce"] == custom_nonce


class TestSignatureVerification:
    """Test signature verification."""

    async def test_verify_valid_signature(self, signing_module):
        """Test verifying a valid signature."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test", "data": "hello"}
        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is True
        assert result.reason is None

    async def test_verify_tampered_payload(self, signing_module):
        """Test verifying a signature with tampered payload."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test", "data": "hello"}
        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        # Tamper with payload
        tampered_payload = {"action": "test", "data": "goodbye"}

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            tampered_payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is False
        assert "Signature mismatch" in result.reason

    async def test_verify_wrong_signature(self, signing_module):
        """Test verifying with wrong signature."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test"}
        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        # Use a different signature
        wrong_signature = "0" * 64

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            wrong_signature,
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is False
        assert "Signature mismatch" in result.reason

    async def test_verify_no_signing_key(self, signing_module):
        """Test verification fails without signing key."""
        payload = {"action": "test"}

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            "signature",
            time.time(),
            "nonce",
        )

        assert result.valid is False
        assert "No signing key found" in result.reason


class TestReplayProtection:
    """Test replay attack protection."""

    async def test_timestamp_too_old(self, signing_module):
        """Test rejection of messages with old timestamps."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test"}

        # Sign with old timestamp (10 minutes ago, max drift is 5 minutes)
        old_time = time.time() - 600
        sig_data = await signing_module.sign_message(
            "agent-1", "msg-123", payload, timestamp=old_time
        )

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is False
        assert "Timestamp too old" in result.reason

    async def test_timestamp_future(self, signing_module):
        """Test rejection of messages with future timestamps."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test"}

        # Sign with future timestamp (10 minutes ahead)
        future_time = time.time() + 600
        sig_data = await signing_module.sign_message(
            "agent-1", "msg-123", payload, timestamp=future_time
        )

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is False
        assert "future" in result.reason

    async def test_nonce_replay_protection(self, signing_module):
        """Test that same nonce cannot be used twice."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test"}
        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        # First verification should succeed
        result1 = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )
        assert result1.valid is True

        # Second verification with same nonce should fail (replay attack)
        result2 = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )
        assert result2.valid is False
        assert "Nonce already used" in result2.reason

    async def test_get_nonce_status(self, signing_module):
        """Test checking nonce status."""
        # New message should not have nonce
        assert await signing_module.get_nonce_status("msg-123") is False

        # After verification, nonce should be recorded
        await signing_module.generate_signing_key("agent-1")
        payload = {"action": "test"}
        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert await signing_module.get_nonce_status("msg-123") is True


class TestCanonicalRepresentation:
    """Test canonical data representation."""

    def test_canonicalize_deterministic(self, signing_module):
        """Test that canonicalization is deterministic."""
        data1 = {"b": 2, "a": 1, "c": 3}
        data2 = {"c": 3, "a": 1, "b": 2}

        canon1 = signing_module._canonicalize(data1)
        canon2 = signing_module._canonicalize(data2)

        # Same data in different order should produce same canonical form
        assert canon1 == canon2

    def test_canonicalize_nested(self, signing_module):
        """Test canonicalization of nested data."""
        data = {
            "outer": {"b": 2, "a": 1},
            "list": [3, 2, 1],
        }

        canonical = signing_module._canonicalize(data)

        # Should be valid JSON and deterministic
        import json

        parsed = json.loads(canonical)
        assert parsed == data


class TestSignatureIntegrity:
    """Test that different messages produce different signatures."""

    async def test_different_payloads_different_signatures(self, signing_module):
        """Test different payloads produce different signatures."""
        await signing_module.generate_signing_key("agent-1")

        payload1 = {"action": "test1"}
        payload2 = {"action": "test2"}

        sig1 = await signing_module.sign_message("agent-1", "msg-1", payload1)
        sig2 = await signing_module.sign_message("agent-1", "msg-2", payload2)

        assert sig1["signature"] != sig2["signature"]

    async def test_different_agents_different_signatures(self, signing_module):
        """Test different agents produce different signatures."""
        await signing_module.generate_signing_key("agent-1")
        await signing_module.generate_signing_key("agent-2")

        payload = {"action": "test"}

        sig1 = await signing_module.sign_message("agent-1", "msg-1", payload)
        sig2 = await signing_module.sign_message("agent-2", "msg-2", payload)

        # Different agents should produce different signatures
        assert sig1["signature"] != sig2["signature"]

    async def test_same_payload_same_signature_with_same_nonce(self, signing_module):
        """Test same payload and nonce produces same signature."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"action": "test"}
        timestamp = time.time()
        nonce = "fixed-nonce"

        sig1 = await signing_module.sign_message(
            "agent-1", "msg-1", payload, timestamp=timestamp, nonce=nonce
        )
        sig2 = await signing_module.sign_message(
            "agent-1", "msg-1", payload, timestamp=timestamp, nonce=nonce
        )

        # Same inputs should produce same signature
        assert sig1["signature"] == sig2["signature"]


class TestEdgeCases:
    """Test edge cases."""

    async def test_empty_payload(self, signing_module):
        """Test signing empty payload."""
        await signing_module.generate_signing_key("agent-1")

        payload = {}
        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is True

    async def test_large_payload(self, signing_module):
        """Test signing large payload."""
        await signing_module.generate_signing_key("agent-1")

        # Create large payload
        payload = {"data": "x" * 10000, "numbers": list(range(1000))}

        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is True

    async def test_unicode_payload(self, signing_module):
        """Test signing payload with unicode characters."""
        await signing_module.generate_signing_key("agent-1")

        payload = {"message": "Hello 世界 🌍", "emoji": "🚀💻"}

        sig_data = await signing_module.sign_message("agent-1", "msg-123", payload)

        result = await signing_module.verify_signature(
            "agent-1",
            "msg-123",
            payload,
            sig_data["signature"],
            sig_data["timestamp"],
            sig_data["nonce"],
        )

        assert result.valid is True
