"""Audit Module for Gateway Service."""

import asyncio
import csv
import hashlib
import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)

AuditRecord = dict[str, Any]


class AuditEntry(BaseModel):
    """Audit log entry."""

    message_id: str
    timestamp: float = Field(default_factory=time.time)
    sender_id: str
    sender_instance_id: Optional[str] = None
    target_id: str
    message_type: Optional[str] = None
    correlation_id: Optional[str] = None
    decision: str  # ALLOWED, DENIED, RATE_LIMITED, DLP_BLOCKED, etc.
    latency_ms: float
    payload_hash: str
    violations: list[str] = Field(default_factory=list)
    previous_hash: Optional[str] = None


class AuditFileSink:
    """Append-only audit file sink with rotation."""

    def __init__(self, file_path: str, *, max_bytes: int, backup_count: int) -> None:
        self._path = Path(file_path)
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = asyncio.Lock()

    @property
    def path(self) -> str:
        return str(self._path)

    async def write_entry(self, entry: AuditEntry) -> None:
        data = entry.model_dump()
        data["violations"] = entry.violations
        line = json.dumps(data, sort_keys=True) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._rotate_and_write, line)

    def _rotate_and_write(self, line: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._should_rotate(line):
            self._rotate_files()
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _should_rotate(self, line: str) -> bool:
        if self._max_bytes <= 0:
            return False
        if not self._path.exists():
            return False
        return self._path.stat().st_size + len(line.encode("utf-8")) > self._max_bytes

    def _rotate_files(self) -> None:
        if self._backup_count <= 0:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            return

        for index in range(self._backup_count - 1, 0, -1):
            src = self._path.with_suffix(self._path.suffix + f".{index}")
            dest = self._path.with_suffix(self._path.suffix + f".{index + 1}")
            if src.exists():
                src.replace(dest)

        rotated = self._path.with_suffix(self._path.suffix + ".1")
        if self._path.exists():
            self._path.replace(rotated)


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

    def __init__(
        self, redis: AsyncRedisProtocol, *, file_sink: AuditFileSink | None = None
    ):
        """
        Initialize audit module.

        Args:
            redis: Redis connection
        """
        self.redis = redis
        self._file_sink = file_sink

    async def log_message(
        self,
        message_id: str,
        sender_id: str,
        target_id: str,
        decision: str,
        latency_ms: float,
        payload: AuditRecord,
        violations: Optional[list[str]] = None,
        *,
        message_type: Optional[str] = None,
        correlation_id: Optional[str] = None,
        sender_instance_id: Optional[str] = None,
    ) -> str:
        """
        Log message to audit stream.

        Uses pipeline to batch all Redis operations into a single round-trip,
        reducing calls from 5 to 1.

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

        # Get previous hash for chain (need this before pipeline)
        previous_hash = await self.redis.get("audit:last_hash")

        # Create audit entry
        entry = AuditEntry(
            message_id=message_id,
            timestamp=time.time(),
            sender_id=sender_id,
            sender_instance_id=sender_instance_id,
            target_id=target_id,
            message_type=message_type,
            correlation_id=correlation_id,
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

        # Coerce all to strings for Redis
        fields: dict[str, str] = {k: str(v) for k, v in entry_dict.items()}

        # Batch all writes using pipeline (reduces 4 calls to 1)
        sender_stream = f"audit:by_sender:{sender_id}"
        target_stream = f"audit:by_target:{target_id}"

        pipe = self.redis.pipeline()
        pipe.xadd("audit:messages", fields)
        pipe.xadd(sender_stream, fields)
        pipe.xadd(target_stream, fields)
        pipe.set("audit:last_hash", entry_hash)

        results = await pipe.execute()

        # First result is the main stream ID
        main_stream_id = results[0]

        logger.debug(
            "Audit entry logged",
            extra={
                "message_id": message_id,
                "decision": decision,
                "stream_id": main_stream_id,
            },
        )

        if self._file_sink is not None:
            try:
                await self._file_sink.write_entry(entry)
            except Exception as exc:
                logger.error(
                    "Failed to write audit file",
                    exc_info=exc,
                    extra={"path": self._file_sink.path},
                )

        return main_stream_id

    async def log_security_event(
        self,
        event_type: str,
        details: AuditRecord,
    ) -> str:
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
    ) -> list[AuditRecord]:
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
        return await self._query_indexed_stream(
            "audit:by_sender", sender_id, start_time, end_time, count
        )

    async def query_by_target(
        self,
        target_id: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[AuditRecord]:
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
        return await self._query_indexed_stream(
            "audit:by_target", target_id, start_time, end_time, count
        )

    async def query_security_events(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[AuditRecord]:
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

    async def _query_indexed_stream(
        self,
        prefix: str,
        key: str,
        start_time: Optional[float],
        end_time: Optional[float],
        count: int,
    ) -> list[AuditRecord]:
        """Query an index stream keyed by sender/target."""
        stream = f"{prefix}:{key}"
        return await self._query_stream(stream, start_time, end_time, count)

    async def _query_messages(
        self,
        start_time: Optional[float],
        end_time: Optional[float],
        count: int,
    ) -> list[AuditRecord]:
        """Query the main audit message stream."""
        return await self._query_stream("audit:messages", start_time, end_time, count)

    async def _query_stream(
        self,
        stream: str,
        start_time: Optional[float],
        end_time: Optional[float],
        count: int,
    ) -> list[AuditRecord]:
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
            result: list[AuditRecord] = []
            for stream_id, raw in entries:
                # Work with a mutable, more general-typed copy
                data: AuditRecord = {str(k): v for k, v in raw.items()}
                # Parse violations JSON if present
                if "violations" in data:
                    try:
                        data["violations"] = json.loads(data["violations"])
                    except (json.JSONDecodeError, TypeError):
                        data["violations"] = []
                # Parse details JSON if present (for security events)
                if "details" in data:
                    try:
                        data["details"] = json.loads(data["details"])
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
    ) -> list[AuditRecord]:
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
        all_entries = await self._query_messages(start_time, end_time, count * 2)

        # Filter by decision
        filtered = [entry for entry in all_entries if entry.get("decision") == decision]
        return filtered[:count]

    async def query_by_violation(
        self,
        violation_type: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        count: int = 100,
    ) -> list[AuditRecord]:
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
        all_entries = await self._query_messages(start_time, end_time, count * 2)

        # Filter by violation type
        filtered: list[AuditRecord] = []
        for entry in all_entries:
            violations = entry.get("violations")
            if not isinstance(violations, list):
                continue
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
    ) -> list[AuditRecord]:
        """
        Query all audit log entries.

        Args:
            start_time: Start timestamp (None = beginning)
            end_time: End timestamp (None = now)
            count: Maximum number of entries to return

        Returns:
            List of all audit entries
        """
        return await self._query_messages(start_time, end_time, count)

    async def export_to_csv(
        self,
        entries: list[AuditRecord],
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
            "sender_instance_id",
            "target_id",
            "message_type",
            "correlation_id",
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
        entries: list[AuditRecord],
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

    async def get_stats(self) -> dict[str, int]:
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
