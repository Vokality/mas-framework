"""Regression coverage for gateway enforcement and telemetry contracts."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Never

import pytest
from mas_core.sessions import SessionLeaseSettings, SessionLeaseStore
from mas_gateway.audit import AuditModule
from mas_gateway.authorization import AuthorizationModule
from mas_gateway.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerModule,
    CircuitState,
)
from mas_gateway.config import GatewaySettings
from mas_gateway.dlp import ActionPolicy, DLPModule, DlpRule
from mas_gateway.rate_limit import RateLimitModule
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.typing import EncodableT, FieldT

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("target_status", ["ACTIVE", "INACTIVE", None])
async def test_rbac_cannot_override_explicit_block_or_inactive_target(
    redis: Redis, target_status: str | None
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    if target_status is not None:
        await redis.hset("agent:target", mapping={"status": target_status})
    await auth.create_role("sender", permissions=["send:*"])
    await auth.assign_role("sender", "sender")
    if target_status == "ACTIVE":
        await SessionLeaseStore(redis, SessionLeaseSettings()).acquire("target", "live")
        await auth.set_permissions("sender", blocked_targets=["target"])
    assert await auth.authorize("sender", "target") is False


async def test_repeated_message_ids_consume_rate_quota(redis: Redis) -> None:
    limiter = RateLimitModule(redis, default_per_minute=2, default_per_hour=10)
    assert (await limiter.check_rate_limit("sender", "same-id")).allowed
    assert (await limiter.check_rate_limit("sender", "same-id")).allowed
    assert (await limiter.check_rate_limit("sender", "same-id")).allowed is False


async def test_target_access_uses_live_leases_instead_of_stale_agent_status(
    redis: Redis,
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    await auth.create_role("sender-role", permissions=["send:*"])
    await auth.assign_role("sender", "sender-role")
    await redis.hset("agent:target", mapping={"status": "ACTIVE"})
    assert not await auth.authorize("sender", "target")
    leases = SessionLeaseStore(redis, SessionLeaseSettings())
    lease = await leases.acquire("target", "live")
    await redis.hset("agent:target", mapping={"status": "INACTIVE"})
    assert await auth.authorize("sender", "target")
    await leases.release(lease)
    assert not await auth.authorize("sender", "target")


async def test_rejected_hour_request_does_not_consume_minute_quota(
    redis: Redis,
) -> None:
    limiter = RateLimitModule(redis, default_per_minute=10, default_per_hour=0)
    assert (await limiter.check_rate_limit_legacy("sender", "message")).allowed is False
    assert await limiter.get_current_usage("sender") == {"per_minute": 0, "per_hour": 0}


async def test_reset_rate_limits_treats_agent_id_literally(redis: Redis) -> None:
    limiter = RateLimitModule(redis, default_per_minute=2, default_per_hour=10)
    await limiter.check_rate_limit("sender", "message")
    await limiter.check_rate_limit("*", "message")
    await limiter.reset_limits("*")
    assert await limiter.get_current_usage("sender") == {"per_minute": 1, "per_hour": 1}


async def test_rate_reset_is_oldest_admitted_request_expiration(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_000.125
    monkeypatch.setattr(time, "time", lambda: now)
    limiter = RateLimitModule(redis, default_per_minute=1, default_per_hour=10)
    await limiter.check_rate_limit("sender", "first")
    now += 10
    result = await limiter.check_rate_limit("sender", "second")
    assert result.allowed is False
    assert result.reset_time == pytest.approx(1_060.125)


async def test_invalid_custom_limits_do_not_write_partial_state(
    redis: Redis,
) -> None:
    limiter = RateLimitModule(redis, default_per_minute=1, default_per_hour=10)
    with pytest.raises(ValueError):
        await limiter.set_limits("sender", per_minute=5, per_hour=-1)
    assert await redis.hgetall("ratelimit:sender:limits") == {}


async def test_concurrent_circuit_failures_are_not_lost(redis: Redis) -> None:
    config = CircuitBreakerConfig(failure_threshold=50)
    breakers = [CircuitBreakerModule(redis, config) for _ in range(20)]
    await asyncio.gather(*(breaker.record_failure("target") for breaker in breakers))
    status = await breakers[0].check_circuit("target")
    assert status.failure_count == 20


async def test_circuit_retains_history_for_configured_window(redis: Redis) -> None:
    breaker = CircuitBreakerModule(
        redis, CircuitBreakerConfig(timeout_seconds=0.1, window_seconds=300)
    )
    await breaker.record_failure("target")
    assert await redis.ttl("circuit:target") >= 299


async def test_circuit_epoch_zero_transitions_and_listing_is_read_only(
    redis: Redis,
) -> None:
    now = 0.0
    breaker = CircuitBreakerModule(
        redis,
        CircuitBreakerConfig(failure_threshold=1, timeout_seconds=1),
        clock=lambda: now,
    )
    await breaker.record_failure("target:circuit:one")
    now = 2.0
    before = await redis.hgetall("circuit:target:circuit:one")
    circuits = await breaker.get_all_circuits()
    assert "target:circuit:one" in circuits
    assert await redis.hgetall("circuit:target:circuit:one") == before
    assert (
        await breaker.check_circuit("target:circuit:one")
    ).state == CircuitState.HALF_OPEN


async def test_audit_read_values_are_typed_and_zero_count_is_empty(
    redis: Redis,
) -> None:
    audit = AuditModule(redis, file_sink=None)
    await audit.log_message(
        "message", "sender", "target", "ALLOWED", 1.25, {}, violations=["ssn"]
    )
    records = await audit.query_all()
    assert isinstance(records[0]["timestamp"], float)
    assert records[0]["latency_ms"] == 1.25
    assert await audit.query_by_violation("ssn", count=0) == []
    assert await audit.query_all(count=0) == []


async def test_audit_end_time_includes_every_entry_in_millisecond(
    redis: Redis,
) -> None:
    audit = AuditModule(redis, file_sink=None)
    raw: dict[FieldT, EncodableT] = {
        "message_id": "message",
        "timestamp": "1000.0",
        "sender_id": "sender",
        "target_id": "target",
        "decision": "ALLOWED",
        "latency_ms": "1.0",
        "payload_hash": "hash",
        "violations": json.dumps([]),
    }
    await redis.xadd("audit:messages", raw, id="1000000-0")
    await redis.xadd("audit:messages", raw, id="1000000-1")
    assert len(await audit.query_all(end_time=1000.0)) == 2


async def test_dlp_scans_decoded_unicode_and_escaped_content() -> None:
    module = DLPModule(
        custom_policies={},
        custom_rules=[
            DlpRule(
                id="unicode", type="secret", pattern="秘密", action=ActionPolicy.REDACT
            )
        ],
        merge_strategy="replace",
        disable_defaults=[],
    )
    result = await module.scan({"message": "秘密"})
    assert result.clean is False
    assert result.redacted_payload == {"message": "[REDACTED SECRET]"}


async def test_dlp_redacts_sensitive_numbers_keys_and_newlines() -> None:
    module = DLPModule(
        custom_policies={},
        custom_rules=[],
        merge_strategy="append",
        disable_defaults=[],
    )
    result = await module.scan(
        {"123-45-6789": {"numeric": 123456789, "newline": "123\n45\n6789", "count": 2}}
    )
    assert result.action == ActionPolicy.REDACT
    assert result.redacted_payload == {
        "XXX-XX-6789": {"numeric": "XXX-XX-6789", "newline": "XXX-XX-6789", "count": 2}
    }


async def test_dlp_detects_structured_credentials() -> None:
    module = DLPModule(
        custom_policies={},
        custom_rules=[],
        merge_strategy="append",
        disable_defaults=[],
    )
    result = await module.scan({"credentials": {"password": "MySecretPass123"}})
    assert result.action == ActionPolicy.BLOCK
    assert any(
        violation.violation_type == "password" for violation in result.violations
    )


async def test_dlp_blocks_key_redaction_collisions_without_losing_fields() -> None:
    module = DLPModule(
        custom_policies={},
        custom_rules=[],
        merge_strategy="append",
        disable_defaults=[],
    )
    result = await module.scan({"123-45-6789": "first", "987-65-6789": "second"})
    assert result.action == ActionPolicy.BLOCK
    assert result.redacted_payload is None


async def test_partial_gateway_override_retains_yaml_sibling_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.yaml"
    path.write_text("rate_limit:\n  per_minute: 5\n  per_hour: 30\n")
    settings = GatewaySettings(config_file=str(path), rate_limit={"per_minute": 7})
    assert settings.rate_limit.per_minute == 7
    assert settings.rate_limit.per_hour == 30


async def test_audit_recent_reads_return_newest_typed_records(redis: Redis) -> None:
    audit = AuditModule(redis, file_sink=None)
    await audit.log_message("first", "sender", "target", "ALLOWED", 1.25, {})
    await audit.log_message("second", "sender", "target", "DENIED", 2.5, {})
    records = await audit.query_recent(count=1)
    assert records[0].message_id == "second"
    assert records[0].latency_ms == 2.5
    assert records[0].stream_id


async def test_audit_does_not_invent_missing_timestamps() -> None:
    raw = {
        "message_id": "message",
        "sender_id": "sender",
        "target_id": "target",
        "decision": "ALLOWED",
        "latency_ms": "1.0",
        "payload_hash": "hash",
    }
    assert AuditModule._record_to_entry(raw) is None


async def test_audit_backend_failure_is_visible_to_health_reader(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(**options: object) -> Never:
        raise RedisConnectionError("Redis unavailable")

    monkeypatch.setattr(redis, "pipeline", unavailable)
    with pytest.raises(RedisConnectionError):
        await AuditModule(redis, file_sink=None).get_stats()


async def test_replacing_acl_keeps_explicit_blocks_continuously_active(
    redis: Redis,
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=False)
    await redis.hset("agent:target", mapping={"status": "ACTIVE"})
    await SessionLeaseStore(redis, SessionLeaseSettings()).acquire("target", "live")
    await auth.set_permissions(
        "sender", allowed_targets=["*"], blocked_targets=["target"]
    )

    async def replace_blocks() -> None:
        for _ in range(50):
            await auth.set_permissions("sender", blocked_targets=["target"])

    async def check_access() -> None:
        for _ in range(150):
            assert await auth.authorize("sender", "target") is False

    await asyncio.gather(replace_blocks(), check_access())
