"""Queued validation coalesces current snapshots without reusing stale results."""

from __future__ import annotations

import asyncio
import gc
import json
import threading
import weakref
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.verification import VerificationError
from mas_server import authn as authn_module
from mas_server import tls as tls_module
from mas_server.dev import generate_dev_tls
from mas_server.peer_validation import QueuedPeerValidator
from mas_server.tls import PeerCertificatePolicy
from mas_server.types import TlsConfig


@pytest.mark.asyncio
async def test_queued_duplicates_share_worker_check_and_fail_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls: list[tuple[bytes, bytes]] = []
    threads: list[int] = []
    policy = PeerCertificatePolicy(TlsConfig("unused", "unused", "unused"))

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        calls.append((leaf, chain))
        threads.append(threading.get_ident())
        if leaf == b"good":
            entered.set()
            if not release.wait(1):
                raise TimeoutError("test validation was not released")
        else:
            raise ValueError("rejected certificate")

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    validator = QueuedPeerValidator(policy)
    tasks = [
        asyncio.create_task(validator.validate(b"good", b"chain")) for _ in range(25)
    ]
    rejected = asyncio.create_task(validator.validate(b"bad", b"chain"))
    different_chain = asyncio.create_task(validator.validate(b"good", b"other-chain"))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        assert not validator.idle
        # The loop progresses while the actual worker owns its frozen snapshot.
        await asyncio.sleep(0.001)
        assert not any(task.done() for task in tasks)
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
        release.set()
        await asyncio.gather(*tasks)
        await different_chain
        with pytest.raises(ValueError, match="rejected"):
            await rejected
        assert calls == [
            (b"good", b"chain"),
            (b"bad", b"chain"),
            (b"good", b"other-chain"),
        ]
        assert validator._active == validator._pending_bytes == 0
        assert validator._worker is None
        assert validator.idle
        await validator.validate(b"good", b"chain")
        assert calls[-1] == (b"good", b"chain") and len(calls) == 4
    finally:
        release.set()
        await asyncio.gather(*tasks, rejected, different_chain, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = 0

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        if not release.wait(1):
            raise TimeoutError("test validation was not released")
        raise ValueError("current peer rejected")

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    validator = QueuedPeerValidator(
        PeerCertificatePolicy(TlsConfig("unused", "unused", "unused"))
    )
    first = asyncio.create_task(validator.validate(b"leaf", b"chain"))
    second = asyncio.create_task(validator.validate(b"leaf", b"chain"))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not second.done()
        release.set()
        with pytest.raises(ValueError, match="current peer rejected"):
            await second
        await asyncio.sleep(0)
        assert calls == 1
        assert validator._worker is None
        assert validator._active == 0
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_all_cancelled_waiters_release_budgets_and_worker_remains_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = 0

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        if not release.wait(1):
            raise TimeoutError("test validation was not released")
        if leaf == b"rejected":
            raise ValueError("current peer rejected")

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    validator = QueuedPeerValidator(
        PeerCertificatePolicy(TlsConfig("unused", "unused", "unused"))
    )
    tasks = [
        asyncio.create_task(validator.validate(b"rejected", b"chain")) for _ in range(2)
    ]
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert not validator.idle
        release.set()
        async with asyncio.timeout(0.5):
            while not validator.idle:
                await asyncio.sleep(0)
        assert validator._active == validator._pending_bytes == 0
        await validator.validate(b"accepted", b"chain")
        assert calls == 2 and validator.idle
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revocation", "trust", "expiry"])
async def test_arrival_after_snapshot_rechecks_changed_security_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    bundle = await asyncio.to_thread(
        generate_dev_tls, tmp_path / "tls", agent_ids=frozenset({"worker"})
    )
    revoked = tmp_path / "revoked.json"
    revoked.write_text("[]")
    leaf_bytes = Path(bundle.client("worker").client_cert_path).read_bytes()
    leaf = x509.load_pem_x509_certificate(leaf_bytes)
    replacement_ca: bytes | None = None
    if change == "trust":
        other = await asyncio.to_thread(generate_dev_tls, tmp_path / "other")
        replacement_ca = Path(other.ca_pem).read_bytes()
    policy = PeerCertificatePolicy(
        TlsConfig(bundle.server_cert, bundle.server_key, bundle.ca_pem, str(revoked))
    )
    validator = QueuedPeerValidator(policy)
    entered, release = threading.Event(), threading.Event()
    calls = 0
    original = PeerCertificatePolicy.validate

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        nonlocal calls
        calls += 1
        original(policy, leaf, chain)
        if calls == 1:
            # The first check has taken its real trust/time snapshot already.
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test snapshot was not released")

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    first = asyncio.create_task(validator.validate(leaf_bytes, b""))
    later: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        later = asyncio.create_task(validator.validate(leaf_bytes, b""))
        await asyncio.sleep(0)
        assert not later.done()
        if change == "revocation":
            replacement = revoked.with_suffix(".new")
            replacement.write_text(
                json.dumps([leaf.fingerprint(hashes.SHA256()).hex()])
            )
            replacement.replace(revoked)
        elif change == "trust":
            assert replacement_ca is not None
            replacement = Path(bundle.ca_pem).with_suffix(".new")
            replacement.write_bytes(replacement_ca)
            replacement.replace(bundle.ca_pem)
        else:
            expired = leaf.not_valid_after_utc + timedelta(seconds=1)

            class Clock:
                @staticmethod
                def now(tz: tzinfo | None = None) -> datetime:
                    return expired.astimezone(tz or UTC)

            monkeypatch.setattr(tls_module, "datetime", Clock)
        release.set()
        await first
        with pytest.raises((ValueError, VerificationError)):
            await later
        assert calls == 2
        assert validator._worker is None
    finally:
        release.set()
        await asyncio.gather(
            first, *([later] if later is not None else []), return_exceptions=True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity,max_bytes", [(1, 100), (4, 7)])
async def test_pending_count_and_bytes_are_bounded(
    monkeypatch: pytest.MonkeyPatch, capacity: int, max_bytes: int
) -> None:
    entered, release = threading.Event(), threading.Event()

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        entered.set()
        if not release.wait(1):
            raise TimeoutError("test validation was not released")

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    validator = QueuedPeerValidator(
        PeerCertificatePolicy(TlsConfig("unused", "unused", "unused")),
        capacity=capacity,
        max_pending_bytes=max_bytes,
    )
    first = asyncio.create_task(validator.validate(b"leaf", b"key"))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        with pytest.raises(ValueError, match="capacity"):
            await validator.validate(b"leaf", b"key")
        assert validator._active == 1 and validator._pending_bytes == 7
        release.set()
        await first
        await validator.validate(b"leaf", b"key")
        assert validator._active == validator._pending_bytes == 0
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


def test_registry_isolates_event_loops_without_retaining_finished_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(authn_module, "_VALIDATORS", weakref.WeakKeyDictionary())
    tls = TlsConfig("unused", "unused", "unused")

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        pass

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)

    async def check() -> QueuedPeerValidator:
        validator = authn_module._peer_validator(tls)
        await validator.validate(b"leaf", b"chain")
        return validator

    with asyncio.Runner() as first_runner:
        first = first_runner.run(check())
        first_loop = weakref.ref(first_runner.get_loop())
    with asyncio.Runner() as second_runner:
        second = second_runner.run(check())
        second_loop = weakref.ref(second_runner.get_loop())
        assert second is not first
        with pytest.raises(RuntimeError, match="another event loop"):
            second_runner.run(first.validate(b"leaf", b"chain"))
    assert first._worker is second._worker is None
    del first, second
    gc.collect()
    assert not authn_module._VALIDATORS
    # Runner.close clears native loop ownership; validators add no global roots.
    assert first_loop() is None and second_loop() is None


@pytest.mark.asyncio
async def test_same_tls_configuration_validates_on_concurrent_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(authn_module, "_VALIDATORS", weakref.WeakKeyDictionary())
    entered = threading.Barrier(2)
    tls = TlsConfig("unused", "unused", "unused")

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        entered.wait(timeout=1)

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)

    async def check() -> QueuedPeerValidator:
        validator = authn_module._peer_validator(tls)
        await validator.validate(b"leaf", b"chain")
        return validator

    def run() -> QueuedPeerValidator:
        with asyncio.Runner() as runner:
            return runner.run(check())

    first, second = await asyncio.gather(asyncio.to_thread(run), asyncio.to_thread(run))
    assert first is not second
    assert first._worker is second._worker is None
    assert first._active == second._active == 0


@pytest.mark.asyncio
async def test_registry_evicts_idle_configuration_without_orphaning_busy_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(authn_module, "_VALIDATORS", weakref.WeakKeyDictionary())
    entered, release = threading.Event(), threading.Event()

    def validate(policy: PeerCertificatePolicy, leaf: bytes, chain: bytes) -> None:
        entered.set()
        if not release.wait(1):
            raise TimeoutError("test validation was not released")

    monkeypatch.setattr(PeerCertificatePolicy, "validate", validate)
    configurations = [TlsConfig(str(index), "unused", "unused") for index in range(33)]
    busy = authn_module._peer_validator(configurations[0])
    checking = asyncio.create_task(busy.validate(b"leaf", b"chain"))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        assert not busy.idle
        for configuration in configurations[1:]:
            authn_module._peer_validator(configuration)
        cached = authn_module._VALIDATORS[asyncio.get_running_loop()]
        assert len(cached) == 32
        assert cached[configurations[0]] is busy
        assert configurations[1] not in cached
        release.set()
        await checking
        assert busy.idle
    finally:
        release.set()
        await asyncio.gather(checking, return_exceptions=True)
