"""Ingress success metrics follow the durable audited commit receipt."""

import asyncio

import pytest
from mas_core import EnvelopeMessage
from mas_core.durability import RedisDurability, RedisDurabilityError
from mas_core.sessions import SessionLeaseStore
from mas_core.telemetry.runtime import TelemetryRuntime
from mas_gateway.audit import AuditModule
from mas_gateway.authorization import AuthorizationModule
from mas_gateway.rate_limit import RateLimitModule
from mas_server import policy as policy_module
from mas_server.policy import PolicyPipeline
from mas_server.routing import MessageRouter
from redis.asyncio import Redis


class ReceiptGate(RedisDurability):
    """Hold the caller's confirmation after its actual queued Redis writes."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.released = asyncio.Event()

    async def confirm(self, connection: Redis) -> None:
        await super().confirm(connection)
        self.entered.set()
        await self.released.wait()


class UnconfirmedReceipt(RedisDurability):
    """Model an uncertain commit after real Redis writes have completed."""

    async def confirm(self, connection: Redis) -> None:
        await super().confirm(connection)
        raise RedisDurabilityError("configured_durability_unconfirmed")


@pytest.mark.asyncio
@pytest.mark.parametrize("unconfirmed", [False, True])
async def test_ingress_success_requires_commit_receipt(
    redis: Redis, monkeypatch: pytest.MonkeyPatch, unconfirmed: bool
) -> None:
    telemetry = TelemetryRuntime(
        enabled=False, tracer=None, tracer_provider=None, meter_provider=None
    )
    monkeypatch.setattr(policy_module, "get_telemetry", lambda: telemetry)
    authz = AuthorizationModule(redis, enable_rbac=True)
    await authz.create_role("producer", permissions=["send:worker"])
    await authz.assign_role("sender", "producer")
    leases = SessionLeaseStore(redis)
    lease = await leases.acquire("worker", "instance")
    gate = ReceiptGate()
    durability = UnconfirmedReceipt() if unconfirmed else gate
    audit = AuditModule(
        redis,
        file_sink=None,
        durability=durability,
    )
    policy = PolicyPipeline(
        authz=authz,
        rate_limit=RateLimitModule(redis, 100, 1000),
        audit=audit,
        router=MessageRouter(redis=redis, dlq_enabled=True, durability=durability),
        dlp=None,
        circuit_breaker=None,
    )
    message = EnvelopeMessage(
        sender_id="sender", target_id="worker", message_type="work", data={}
    )
    task = asyncio.create_task(policy.ingest_and_route(message))
    try:
        if unconfirmed:
            with pytest.raises(RedisDurabilityError):
                await task
            assert telemetry.snapshot().ingress == {}
            assert telemetry.snapshot().policy_samples == 0
        else:
            await asyncio.wait_for(gate.entered.wait(), 2)
            assert telemetry.snapshot().ingress == {}
            assert telemetry.snapshot().policy_samples == 0
            gate.released.set()
            assert await task == message.message_id
            assert telemetry.snapshot().ingress == {"ALLOWED": 1}
            assert telemetry.snapshot().policy_samples == 1
            assert telemetry.snapshot().policy_latency_mean_ms > 0
        assert await redis.xlen("agent.stream:worker") == 1
        assert await redis.xlen("audit:messages") == 1
        assert await audit.verify_integrity(message.message_id)
    finally:
        gate.released.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await audit.close()
        await leases.release(lease)
