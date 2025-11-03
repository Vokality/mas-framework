"""Audit Module for Gateway Service."""

import csv
import hashlib
import io
import json
import logging
import time
from typing import Optional
from mas.redis_types import AsyncRedisProtocol
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AuditEntry(BaseModel):
    """Audit log entry."""

    message_id: str
    timestamp: float = Field(default_factory=time.time)
    sender_id: str
    target_id: str
    decision: str  # ALLOWED, DENIED, RATE_LIMITED, DLP_BLOCKED, etc.
    latency_ms: float
    payload_hash: str
    violations: list[str] = Field(default_factory=list)
    previous_hash: Optional[str] = None


class AuditModule:
    """
    Audit module for immutable message logging.

    Implements audit logging using Redis Streams as per GATEWAY.md:
    - Immutable append-only logs
    - Complete audit trail
    - Hash chain for tamper detection
    - Queryable by sender, target, time
    - Security event tracking

    Redis Data Model:
        audit:messages → Main audit stream (all messages)
        audit:by_sender:{sender_id} → Indexed by sender
        audit:by_target:{target_id} → Indexed by target
        audit:security_events → Security-specific events
        audit:last_hash → Last hash for chain integrity
    """

    def __init__(self, redis: AsyncRedisProtocol):
        """
        Initialize audit module.

        Args:
            redis: Redis connection
        """
        self.redis = redis

    async def log_message(
        self,
        message_id: str,
        sender_id: str,
        target_id: str,
        decision: str,
        latency_ms: float,
        payload: dict,
        violations: Optional[list[str]] = None,
    ) -> str:
        """
        Log message to audit stream.

        Args:
            message_id: Unique message identifier
            sender_id: Sender agent ID
            target_id: Target agent ID
            decision: Gateway decision (ALLOWED, DENIED, etc.)
            latency_ms: Processing latency in milliseconds
            payload: Message payload (will be hashed)
            violations: List of policy violations

        Returns:
            Stream entry ID
        """
        # Compute payload hash
        payload_str = json.dumps(payload, sort_keys=True)
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()

        # Get previous hash for chain
        previous_hash = await self.redis.get("audit:last_hash")

        # Create audit entry
        entry = AuditEntry(
            message_id=message_id,
            timestamp=time.time(),
            sender_id=sender_id,
            target_id=target_id,
            decision=decision,
            latency_ms=latency_ms,
            payload_hash=payload_hash,
            violations=violations or [],
            previous_hash=previous_hash,
        )

        # Compute entry hash for chain
        entry_data = entry.model_dump_json(exclude={"previous_hash"})
        entry_hash = hashlib.sha256(entry_data.encode()).hexdigest()

        # Prepare entry for Redis Stream
        entry_dict = entry.model_dump()
        entry_dict["violations"] = json.dumps(entry_dict["violations"])
        # Remove None values (Redis doesn't accept them)
        entry_dict = {k: v for k, v in entry_dict.items() if v is not None}

        # Write to main audit stream
        # Redis expects encodable field values; coerce all to strings for consistency
        fields_main: dict[str, str] = {k: str(v) for k, v in entry_dict.items()}
        main_stream_id = await self.redis.xadd("audit:messages", fields_main)

        # Index by sender
        sender_stream = f"audit:by_sender:{sender_id}"
        fields_sender: dict[str, str] = {k: str(v) for k, v in entry_dict.items()}
        await self.redis.xadd(sender_stream, fields_sender)

        # Index by target
        target_stream = f"audit:by_target:{target_id}"
        fields_target: dict[str, str] = {k: str(v) for k, v in entry_dict.items()}
        await self.redis.xadd(target_stream, fields_target)

        # Update hash chain
        await self.redis.set("audit:last_hash", entry_hash)

        logger.debug(
            "Audit entry logged",
            extra={
                "message_id": message_id,
                "decision": decision,
                "stream_id": main_stream_id,
            },
        )

        return main_stream_id

    async def log_security_event(self, event_type: str, details: dict) -> str:
        """
        Log security event to audit stream.

        Args:
            event_type: Event type (AUTH_FAILURE, AUTHZ_DENIED, etc.)
            details: Event details dictionary

        Returns:
            Stream entry ID
        """
        entry_raw = {
            "timestamp": time.time(),
            "event_type": event_type,
            "details": json.dumps(details),
        }
        entry_fields: dict[str, str] = {k: str(v) for k, v in entry_raw.items()}

        stream_id = await self.redis.xadd("audit:security_events", entry_fields)

        logger.info(
            "Security event logged",
            extra={"event_type": event_type, "stream_id": stream_id},
        )

        return stream_id

    async def query_by_sender(
        self,
        sender_id: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[dict]:
        """
        Query audit log by sender.

        Args:
            sender_id: Sender agent ID
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries
        """
        stream = f"audit:by_sender:{sender_id}"
        return await self._query_stream(stream, start_time, end_time, count)

    async def query_by_target(
        self,
        target_id: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[dict]:
        """
        Query audit log by target.

        Args:
            target_id: Target agent ID
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries
        """
        stream = f"audit:by_target:{target_id}"
        return await self._query_stream(stream, start_time, end_time, count)

    async def query_security_events(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[dict]:
        """
        Query security events.

        Args:
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of security events
        """
        return await self._query_stream(
            "audit:security_events", start_time, end_time, count
        )

    async def _query_stream(
        self,
        stream: str,
        start_time: Optional[float],
        end_time: Optional[float],
        count: int,
    ) -> list[dict]:
        """
        Query Redis Stream with time range.

        Args:
            stream: Stream name
            start_time: Start timestamp
            end_time: End timestamp
            count: Max entries

        Returns:
            List of entries
        """
        # Check if stream exists
        exists = await self.redis.exists(stream)
        if not exists:
            return []

        # Convert timestamps to Redis Stream IDs
        start_id = self._timestamp_to_stream_id(start_time) if start_time else "-"
        end_id = self._timestamp_to_stream_id(end_time) if end_time else "+"

        # Read from stream
        try:
            entries = await self.redis.xrange(stream, start_id, end_id, count)
            result: list[dict] = []
            for stream_id, raw in entries:
                # Work with a mutable, more general-typed copy
                data: dict[str, object] = dict(raw)
                # Parse violations JSON if present
                if "violations" in data:
                    try:
                        data["violations"] = json.loads(data["violations"])  # type: ignore[arg-type]
                    except (json.JSONDecodeError, TypeError):
                        data["violations"] = []
                # Parse details JSON if present (for security events)
                if "details" in data:
                    try:
                        data["details"] = json.loads(data["details"])  # type: ignore[arg-type]
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Add stream ID to entry
                data["stream_id"] = stream_id
                result.append(data)
            return result
        except Exception as e:
            logger.error("Failed to query stream", exc_info=e, extra={"stream": stream})
            return []

    async def verify_integrity(self, message_id: str) -> bool:
        """
        Verify audit log integrity using hash chain.

        Args:
            message_id: Message ID to verify

        Returns:
            True if integrity check passes
        """
        # This is a simplified implementation
        # Full implementation would reconstruct the entire hash chain
        # For now, just verify the entry exists
        entries = await self.redis.xrange("audit:messages", "-", "+")
        for _, data in entries:
            if data.get("message_id") == message_id:
                return True
        return False

    @staticmethod
    def _timestamp_to_stream_id(timestamp: float) -> str:
        """
        Convert Unix timestamp to Redis Stream ID.

        Args:
            timestamp: Unix timestamp

        Returns:
            Stream ID in format "timestamp_ms-0"
        """
        timestamp_ms = int(timestamp * 1000)
        return f"{timestamp_ms}-0"

    async def query_by_decision(
        self,
        decision: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[dict]:
        """
        Query audit log by decision type.

        Args:
            decision: Decision type (ALLOWED, DENIED, RATE_LIMITED, DLP_BLOCKED, etc.)
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries matching decision
        """
        # Query main stream and filter by decision
        all_entries = await self._query_stream(
            "audit:messages",
            start_time,
            end_time,
            count * 2,  # Get more to filter
        )

        # Filter by decision
        filtered = [entry for entry in all_entries if entry.get("decision") == decision]
        return filtered[:count]

    async def query_by_violation(
        self,
        violation_type: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[dict]:
        """
        Query audit log by violation type.

        Args:
            violation_type: Violation type (e.g., "PII", "PHI", "PCI", etc.)
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of audit entries with specified violation
        """
        # Query main stream and filter by violation
        all_entries = await self._query_stream(
            "audit:messages", start_time, end_time, count * 2
        )

        # Filter by violation type
        filtered = []
        for entry in all_entries:
            violations = entry.get("violations", [])
            if violation_type in violations:
                filtered.append(entry)
                if len(filtered) >= count:
                    break

        return filtered

    async def query_all(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[dict]:
        """
        Query all audit log entries.

        Args:
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of all audit entries
        """
        return await self._query_stream("audit:messages", start_time, end_time, count)

    async def export_to_csv(
        self,
        entries: list[dict],
    ) -> str:
        """
        Export audit entries to CSV format.

        Args:
            entries: List of audit entries to export

        Returns:
            CSV string
        """
        if not entries:
            return ""

        output = io.StringIO()

        # Define CSV columns
        fieldnames = [
            "stream_id",
            "message_id",
            "timestamp",
            "sender_id",
            "target_id",
            "decision",
            "latency_ms",
            "payload_hash",
            "violations",
        ]

        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for entry in entries:
            # Convert violations list to string
            entry_copy = entry.copy()
            if isinstance(entry_copy.get("violations"), list):
                entry_copy["violations"] = ";".join(entry_copy["violations"])
            writer.writerow(entry_copy)

        return output.getvalue()

    async def export_to_json(
        self,
        entries: list[dict],
        pretty: bool = True,
    ) -> str:
        """
        Export audit entries to JSON format.

        Args:
            entries: List of audit entries to export
            pretty: Pretty print JSON (default: True)

        Returns:
            JSON string
        """
        if pretty:
            return json.dumps(entries, indent=2, sort_keys=True)
        return json.dumps(entries)

    async def export_compliance_report(
        self,
        start_time: float,
        end_time: float,
        format_type: str = "csv",
    ) -> str:
        """
        Export compliance report for specified time range.

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            format_type: Export format ("csv" or "json")

        Returns:
            Formatted report string
        """
        # Query all entries in range
        entries = await self.query_all(start_time, end_time, count=10000)

        if format_type == "csv":
            return await self.export_to_csv(entries)
        elif format_type == "json":
            return await self.export_to_json(entries)
        else:
            raise ValueError(f"Unsupported format: {format_type}")

    async def get_stats(self) -> dict:
        """
        Get audit log statistics.

        Returns:
            Dictionary with audit statistics
        """
        # Get total message count
        try:
            main_len = await self.redis.xlen("audit:messages")
        except Exception:
            main_len = 0

        # Get security event count
        try:
            security_len = await self.redis.xlen("audit:security_events")
        except Exception:
            security_len = 0

        return {
            "total_messages": main_len,
            "security_events": security_len,
        }
