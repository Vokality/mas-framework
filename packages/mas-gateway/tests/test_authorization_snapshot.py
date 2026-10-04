"""Atomic authorization snapshots reduce reads without retaining stale grants."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from mas_core.sessions import SessionLeaseStore
from mas_gateway.authorization import AuthorizationModule
from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.typing import EncodableT

pytestmark = pytest.mark.asyncio


async def test_many_roles_need_two_authorization_round_trips(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    await SessionLeaseStore(redis).acquire("target", "live")
    for index in range(10):
        role = f"role-{index}"
        await auth.create_role(role, permissions=["send:target"])
        await auth.assign_role("sender", role)
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    assert await auth.authorize("sender", "target")
    assert calls == 2
    assert await auth.authorize("sender", "target")
    assert calls == 3


@pytest.mark.parametrize("mode", ["inactive", "blocked", "acl"])
async def test_early_decisions_do_not_load_role_permissions(
    redis: Redis, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    if mode != "inactive":
        await SessionLeaseStore(redis).acquire("target", "live")
    await auth.set_permissions("sender", allowed_targets=["*"])
    await auth.assign_role("sender", "broken")
    await redis.set("role:broken:permissions", "invalid-type")
    if mode == "blocked":
        await auth.block_target("sender", "target")
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    assert await auth.authorize("sender", "target") is (mode == "acl")
    assert calls == 1


@pytest.mark.parametrize("revocation", ["role", "block", "lease"])
async def test_revocation_between_role_discovery_and_snapshot_denies(
    redis: Redis, monkeypatch: pytest.MonkeyPatch, revocation: str
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    leases = SessionLeaseStore(redis)
    lease = await leases.acquire("target", "live")
    await auth.create_role("grant", permissions=["send:*"])
    await auth.assign_role("sender", "grant")
    changes: dict[str, Callable[[], Awaitable[None]]] = {
        "role": lambda: auth.unassign_role("sender", "grant"),
        "block": lambda: auth.block_target("sender", "target"),
        "lease": lambda: leases.release(lease),
    }
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            await changes[revocation]()
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    assert not await auth.authorize("sender", "target")
    assert calls == 2


async def test_changed_role_set_retries_with_explicit_current_keys(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    await SessionLeaseStore(redis).acquire("target", "live")
    await auth.create_role("send", permissions=["send:*"])
    await auth.create_role("read", permissions=["read:*"])
    await auth.assign_role("sender", "send")
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            await auth.unassign_role("sender", "send")
            await auth.assign_role("sender", "read")
        if calls == 3:
            assert "role:read:permissions" in args[:numkeys]
            assert "role:send:permissions" not in args[:numkeys]
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    assert not await auth.authorize("sender", "target")
    assert calls == 3
    assert await auth.authorize("sender", "target", action="read")


async def test_continuous_role_changes_fail_closed_after_bounded_reads(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    await SessionLeaseStore(redis).acquire("target", "live")
    await auth.create_role("first", permissions=["send:*"])
    await auth.create_role("second", permissions=["send:*"])
    await auth.assign_role("sender", "first")
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        if calls > 1:
            await redis.delete("agent:sender:roles")
            await auth.assign_role("sender", "second" if calls % 2 == 0 else "first")
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    with pytest.raises(RedisError, match="authorization_changed_during_read"):
        await auth.authorize("sender", "target")
    assert calls == 4


async def test_rbac_direct_check_rechecks_role_assignment(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    await auth.create_role("grant", permissions=["send:*"])
    await auth.assign_role("sender", "grant")
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            await auth.unassign_role("sender", "grant")
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    assert not await auth.check_rbac("sender", "send:target")
    assert calls == 2


@pytest.mark.parametrize("revocation", ["permission", "role", "block", "lease"])
async def test_warm_role_hints_never_cache_access_decisions(
    redis: Redis, monkeypatch: pytest.MonkeyPatch, revocation: str
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    leases = SessionLeaseStore(redis)
    lease = await leases.acquire("target", "live")
    await auth.create_role("grant", permissions=["send:*"])
    await auth.assign_role("sender", "grant")
    assert await auth.authorize("sender", "target")
    if revocation == "permission":
        await auth.remove_role_permission("grant", "send:*")
    elif revocation == "role":
        await auth.unassign_role("sender", "grant")
    elif revocation == "block":
        await auth.block_target("sender", "target")
    else:
        await leases.release(lease)
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    assert not await auth.authorize("sender", "target")
    assert calls == 1


async def test_added_role_refreshes_warm_hint_before_granting(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = AuthorizationModule(redis, enable_rbac=True)
    await SessionLeaseStore(redis).acquire("target", "live")
    await auth.create_role("read", permissions=["read:*"])
    await auth.assign_role("sender", "read")
    assert not await auth.authorize("sender", "target")
    await auth.create_role("send", permissions=["send:*"])
    await auth.assign_role("sender", "send")
    original = redis.eval_ro
    calls = 0

    async def evaluate(script: str, numkeys: int, *args: EncodableT) -> object:
        nonlocal calls
        calls += 1
        return await original(script, numkeys, *args)

    monkeypatch.setattr(redis, "eval_ro", evaluate)
    assert await auth.authorize("sender", "target")
    assert calls == 2
