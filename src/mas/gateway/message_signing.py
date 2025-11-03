"""Message Signing Module for Gateway Service."""

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

from pydantic import BaseModel
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class SignatureResult(BaseModel):
    """Result of signature verification."""

    valid: bool
    reason: Optional[str] = None


class MessageSigningModule:
    """
    Message signing module for cryptographic integrity verification.

    Implements Phase 2 message signing as per GATEWAY.md:
    - HMAC-based message signatures
    - Prevents message tampering (T6)
    - Prevents replay attacks (T7) with nonce and timestamp validation
    - Agent-specific signing keys

    Redis Data Model:
        agent:{agent_id}:signing_key → HMAC signing key (secret)
        message_nonces:{message_id} → Nonce tracking (TTL: 5 minutes)

    Security Features:
        - HMAC-SHA256 signatures
        - Per-agent signing keys
        - Nonce-based replay protection
        - Timestamp validation (±5 minute window)
        - Key rotation support
    """

    def __init__(
        self,
        redis: Redis,
        max_timestamp_drift: int = 300,  # 5 minutes
        nonce_ttl: int = 300,  # 5 minutes
    ):
        """
        Initialize message signing module.

        Args:
            redis: Redis connection
            max_timestamp_drift: Max allowed time drift in seconds (default: 300)
            nonce_ttl: TTL for nonce tracking in seconds (default: 300)
        """
        self.redis = redis
        self.max_timestamp_drift = max_timestamp_drift
        self.nonce_ttl = nonce_ttl

    async def generate_signing_key(self, agent_id: str) -> str:
        """
        Generate and store a new signing key for an agent.

        Args:
            agent_id: Agent ID

        Returns:
            Generated signing key (hex encoded)
        """
        # Generate 32-byte (256-bit) key
        key = secrets.token_bytes(32)
        key_hex = key.hex()

        # Store in Redis
        key_field = f"agent:{agent_id}:signing_key"
        await self.redis.set(key_field, key_hex)

        logger.info("Generated signing key", extra={"agent_id": agent_id})

        return key_hex

    async def get_signing_key(self, agent_id: str) -> Optional[str]:
        """
        Get agent's signing key.

        Args:
            agent_id: Agent ID

        Returns:
            Signing key (hex encoded) or None if not found
        """
        key_field = f"agent:{agent_id}:signing_key"
        key = await self.redis.get(key_field)
        return key

    async def rotate_signing_key(self, agent_id: str) -> str:
        """
        Rotate agent's signing key.

        Args:
            agent_id: Agent ID

        Returns:
            New signing key (hex encoded)
        """
        new_key = await self.generate_signing_key(agent_id)

        logger.info("Rotated signing key", extra={"agent_id": agent_id})

        return new_key

    async def sign_message(
        self,
        agent_id: str,
        message_id: str,
        payload: dict,
        timestamp: Optional[float] = None,
        nonce: Optional[str] = None,
    ) -> dict:
        """
        Sign a message with HMAC.

        Args:
            agent_id: Sender agent ID
            message_id: Unique message ID
            payload: Message payload
            timestamp: Message timestamp (defaults to current time)
            nonce: Unique nonce for replay protection (auto-generated if not provided)

        Returns:
            Dictionary with signature, timestamp, and nonce
        """
        # Get or generate timestamp
        if timestamp is None:
            timestamp = time.time()

        # Generate nonce if not provided
        if nonce is None:
            nonce = secrets.token_urlsafe(16)

        # Get signing key
        signing_key = await self.get_signing_key(agent_id)
        if not signing_key:
            raise ValueError(f"No signing key found for agent {agent_id}")

        # Create signature payload
        # Include all fields that should be protected from tampering
        signature_data = {
            "message_id": message_id,
            "sender_id": agent_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "payload": payload,
        }

        # Convert to canonical string representation
        canonical_str = self._canonicalize(signature_data)

        # Generate HMAC signature
        key_bytes = bytes.fromhex(signing_key)
        signature = hmac.new(
            key_bytes, canonical_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        logger.debug(
            "Signed message",
            extra={"agent_id": agent_id, "message_id": message_id},
        )

        return {
            "signature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
        }

    async def verify_signature(
        self,
        agent_id: str,
        message_id: str,
        payload: dict,
        signature: str,
        timestamp: float,
        nonce: str,
    ) -> SignatureResult:
        """
        Verify message signature.

        Args:
            agent_id: Sender agent ID
            message_id: Unique message ID
            payload: Message payload
            signature: HMAC signature to verify
            timestamp: Message timestamp
            nonce: Message nonce

        Returns:
            SignatureResult with validation status and reason
        """
        # Get signing key
        signing_key = await self.get_signing_key(agent_id)
        if not signing_key:
            return SignatureResult(
                valid=False, reason=f"No signing key found for agent {agent_id}"
            )

        # Check timestamp (prevent replay attacks with old messages)
        current_time = time.time()
        time_diff = abs(current_time - timestamp)

        if time_diff > self.max_timestamp_drift:
            return SignatureResult(
                valid=False,
                reason=f"Timestamp too old or future ({time_diff:.1f}s drift)",
            )

        # Check nonce (prevent replay attacks with duplicate nonces)
        nonce_key = f"message_nonces:{message_id}"
        nonce_exists = await self.redis.exists(nonce_key)

        if nonce_exists:
            return SignatureResult(
                valid=False, reason="Nonce already used (replay attack?)"
            )

        # Reconstruct signature payload
        signature_data = {
            "message_id": message_id,
            "sender_id": agent_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "payload": payload,
        }

        # Create canonical representation
        canonical_str = self._canonicalize(signature_data)

        # Compute expected signature
        key_bytes = bytes.fromhex(signing_key)
        expected_signature = hmac.new(
            key_bytes, canonical_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # Compare signatures (timing-safe comparison)
        if not hmac.compare_digest(signature, expected_signature):
            return SignatureResult(valid=False, reason="Signature mismatch")

        # Record nonce to prevent replay
        await self.redis.setex(nonce_key, self.nonce_ttl, "1")

        logger.debug(
            "Signature verified",
            extra={"agent_id": agent_id, "message_id": message_id},
        )

        return SignatureResult(valid=True)

    def _canonicalize(self, data: dict) -> str:
        """
        Create canonical string representation of data for signing.

        This ensures consistent ordering and formatting so signatures
        are deterministic.

        Args:
            data: Dictionary to canonicalize

        Returns:
            Canonical string representation
        """
        import json

        # Sort keys and use compact JSON representation
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    async def delete_signing_key(self, agent_id: str) -> None:
        """
        Delete agent's signing key (e.g., on deregistration).

        Args:
            agent_id: Agent ID
        """
        key_field = f"agent:{agent_id}:signing_key"
        await self.redis.delete(key_field)

        logger.info("Deleted signing key", extra={"agent_id": agent_id})

    async def get_nonce_status(self, message_id: str) -> bool:
        """
        Check if a nonce has been used.

        Args:
            message_id: Message ID

        Returns:
            True if nonce exists (already used), False otherwise
        """
        nonce_key = f"message_nonces:{message_id}"
        exists = await self.redis.exists(nonce_key)
        return bool(exists)
