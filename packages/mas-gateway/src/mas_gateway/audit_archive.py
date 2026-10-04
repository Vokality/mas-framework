"""Durable, immutable audit segments and the checkpoints anchoring live streams."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

AuditStream = Literal["audit:messages", "audit:security_events"]
_MAX_SEGMENT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AuditRetentionSettings:
    """Finite live capacity; trimming requires a durable archive directory."""

    max_messages: int = 100_000
    max_security_events: int = 100_000
    batch_size: int = 1000
    archive_directory: str | None = None

    def __post_init__(self) -> None:
        """Reject unlimited capacity and invalid archive batches."""
        for value in (self.max_messages, self.max_security_events, self.batch_size):
            if isinstance(value, bool) or value <= 0:
                raise ValueError("Audit capacities and batch_size must be positive")
        if self.batch_size > 10_000:
            raise ValueError("Audit archive batch_size must not exceed 10000")
        if self.archive_directory is not None and not self.archive_directory.strip():
            raise ValueError("Audit archive_directory must not be empty")


class AuditCapacityError(RedisError):
    """New writes are refused rather than silently losing audit history."""


class AuditScanLimitError(RedisError):
    """A bounded audit scan stopped without claiming a complete result."""


class AuditArchiveError(RedisError):
    """An archive cannot be written, read or verified safely."""


class AuditCheckpoint(BaseModel):
    """The durably archived prefix that anchors the remaining live chain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stream: AuditStream
    last_stream_id: str = Field(pattern=r"^\d+-\d+$")
    last_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    segment_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    total_entries: int = Field(gt=0)


class ArchivedAuditRow(BaseModel):
    """A stored stream identity and its unchanged field values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_id: str = Field(pattern=r"^\d+-\d+$")
    fields: dict[str, str]


class AuditSegment(BaseModel):
    """A bounded prefix whose predecessor makes archive loss detectable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stream: AuditStream
    previous_checkpoint: AuditCheckpoint | None
    rows: list[ArchivedAuditRow] = Field(min_length=1, max_length=10_000)
    last_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class AuditArchive:
    """Store content-addressed segments before any corresponding Redis trim."""

    def __init__(self, directory: str) -> None:
        """Use an operator-managed directory shared by all writers of a stream."""
        self.directory = Path(directory)

    async def write(self, segment: AuditSegment) -> str:
        """Publish and fsync an immutable segment, returning its content digest."""
        body = segment.model_dump_json().encode()
        if len(body) > _MAX_SEGMENT_BYTES:
            raise AuditArchiveError("audit_archive_segment_too_large")
        digest = hashlib.sha256(body).hexdigest()
        try:
            await asyncio.to_thread(self._write, digest, body)
        except OSError as exc:
            raise AuditArchiveError("audit_archive_write_failed") from exc
        return digest

    def _write(self, digest: str, body: bytes) -> None:
        missing: list[Path] = []
        current = self.directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir(exist_ok=True)
            parent_fd = os.open(directory.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        descriptor, temporary = tempfile.mkstemp(prefix=".audit-", dir=self.directory)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            destination = self.directory / f"{digest}.json"
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                if self._read(digest) != body:
                    raise AuditArchiveError("audit_archive_digest_conflict") from None
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)

    async def read(self, digest: str) -> AuditSegment:
        """Validate file size, content identity and schema at the disk boundary."""
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AuditArchiveError("invalid_audit_archive_digest")
        try:
            body = await asyncio.to_thread(self._read, digest)
            if hashlib.sha256(body).hexdigest() != digest:
                raise AuditArchiveError("audit_archive_digest_mismatch")
            return AuditSegment.model_validate_json(body)
        except (OSError, ValueError) as exc:
            raise AuditArchiveError("audit_archive_read_failed") from exc

    def _read(self, digest: str) -> bytes:
        with (self.directory / f"{digest}.json").open("rb") as handle:
            body = handle.read(_MAX_SEGMENT_BYTES + 1)
        if len(body) > _MAX_SEGMENT_BYTES:
            raise AuditArchiveError("audit_archive_segment_too_large")
        return body
