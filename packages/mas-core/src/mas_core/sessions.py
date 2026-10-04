"""Redis session ownership shared by every broker."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError

_INTEGER = TypeAdapter(int)
_OWNER = TypeAdapter(str | None)

_VALIDATE_KEYS = """
local owner_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
if (owner_type ~= 'none' and owner_type ~= 'string')
    or (index_type ~= 'none' and index_type ~= 'zset') then
    return redis.error_reply('invalid_session_storage_type')
end
"""
_ACQUIRE = (
    _VALIDATE_KEYS
    + """
if not redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2]) then
    return 0
end
local now = redis.call('TIME')
local deadline = now[1] * 1000 + math.floor(now[2] / 1000) + tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', deadline - tonumber(ARGV[2]))
redis.call('ZADD', KEYS[2], deadline, ARGV[3])
return 1
"""
)
_RENEW = (
    _VALIDATE_KEYS
    + """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('PEXPIRE', KEYS[1], ARGV[2])
local now = redis.call('TIME')
local deadline = now[1] * 1000 + math.floor(now[2] / 1000) + tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', deadline - tonumber(ARGV[2]))
redis.call('ZADD', KEYS[2], deadline, ARGV[3])
return 1
"""
)
_RELEASE = (
    _VALIDATE_KEYS
    + """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[2], ARGV[2])
return 1
"""
)
_ACTIVE = """
local now = redis.call('TIME')
local current = now[1] * 1000 + math.floor(now[2] / 1000)
return redis.call('ZCOUNT', KEYS[1], '(' .. current, '+inf')
"""


@dataclass(frozen=True, slots=True)
class SessionLeaseSettings:
    """Bound ownership lifetime and renew before it can expire."""

    ttl_ms: int = 6_000
    renewal_ms: int = 2_000

    def __post_init__(self) -> None:
        """Reserve enough time to fail closed before ownership expires."""
        if self.ttl_ms <= 0 or self.renewal_ms <= 0:
            raise ValueError("session lease intervals must be positive")
        if self.renewal_ms * 2 >= self.ttl_ms:
            raise ValueError("session lease renewal must be less than half its TTL")


@dataclass(frozen=True, slots=True)
class SessionLease:
    """Unique ownership of an agent instance, including across restarts."""

    agent_id: str
    instance_id: str
    owner: str
    expires_at: float

    @property
    def live(self) -> bool:
        """A conservative local bound on confirmed Redis ownership lifetime."""
        return time.monotonic() < self.expires_at


class SessionLeaseConflict(Exception):
    """The instance already has an unexpired owner."""


class SessionLeaseStore:
    """Atomically acquire, renew, and release instance ownership."""

    def __init__(
        self, redis: Redis, settings: SessionLeaseSettings = SessionLeaseSettings()
    ) -> None:
        """Use Redis time and expiry rather than broker wall clocks."""
        self._redis = redis
        self.settings = settings

    @staticmethod
    def _key(agent_id: str, instance_id: str) -> str:
        return f"mas.session:{agent_id}:{instance_id}"

    async def acquire(self, agent_id: str, instance_id: str) -> SessionLease:
        """Reject a duplicate until its current owner releases or expires."""
        lease = SessionLease(
            agent_id,
            instance_id,
            uuid.uuid4().hex,
            time.monotonic() + self.settings.ttl_ms / 1000,
        )
        acquired = _INTEGER.validate_python(
            await self._redis.eval(
                _ACQUIRE,
                2,
                self._key(agent_id, instance_id),
                f"mas.sessions:{agent_id}",
                lease.owner,
                self.settings.ttl_ms,
                instance_id,
            )
        )
        if not acquired:
            raise SessionLeaseConflict("instance_already_connected")
        if not lease.live:
            await self.release(lease)
            raise RedisError("session_lease_expired_before_confirmation")
        return lease

    async def renew(self, lease: SessionLease) -> SessionLease | None:
        """Extend only the exact owner; expired leases cannot be revived."""
        if not lease.live:
            return None
        renewed = replace(
            lease, expires_at=time.monotonic() + self.settings.ttl_ms / 1000
        )
        confirmed = _INTEGER.validate_python(
            await self._redis.eval(
                _RENEW,
                2,
                self._key(lease.agent_id, lease.instance_id),
                f"mas.sessions:{lease.agent_id}",
                lease.owner,
                self.settings.ttl_ms,
                lease.instance_id,
            )
        )
        return renewed if confirmed and renewed.live else None

    async def release(self, lease: SessionLease) -> None:
        """A stale broker cannot delete replacement ownership."""
        await self._redis.eval(
            _RELEASE,
            2,
            self._key(lease.agent_id, lease.instance_id),
            f"mas.sessions:{lease.agent_id}",
            lease.owner,
            lease.instance_id,
        )

    async def owner(self, agent_id: str, instance_id: str) -> str | None:
        """Read only a currently live instance lease."""
        return _OWNER.validate_python(
            await self._redis.get(self._key(agent_id, instance_id))
        )

    async def active(self, agent_id: str) -> bool:
        """Derive activity across brokers without mutating operational data."""
        return bool(
            _INTEGER.validate_python(
                await self._redis.eval(
                    _ACTIVE,
                    1,
                    f"mas.sessions:{agent_id}",
                )
            )
        )
