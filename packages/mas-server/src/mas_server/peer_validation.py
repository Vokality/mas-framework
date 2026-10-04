"""Bounded async peer validation with fresh, worker-owned request snapshots."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from weakref import ref

from .tls import PeerCertificatePolicy


class PeerValidationCapacityError(ValueError):
    """The bounded validator cannot admit another certificate check."""


@dataclass(frozen=True, slots=True)
class _Certificate:
    leaf: bytes
    chain: bytes


@dataclass(slots=True)
class _Validation:
    certificate: _Certificate
    result: asyncio.Future[None]


class QueuedPeerValidator:
    """Coalesce duplicates only within one already-queued validation snapshot.

    Every later snapshot rechecks the synchronous policy, including current
    trust, revocation files and certificate expiry. Instances belong to one
    event loop and exit their worker as soon as accepted work is drained.
    """

    def __init__(
        self,
        policy: PeerCertificatePolicy,
        *,
        capacity: int = 512,
        batch_size: int = 128,
        max_pending_bytes: int = 16_777_216,
    ) -> None:
        if min(capacity, batch_size, max_pending_bytes) <= 0:
            raise ValueError("Peer validation limits must be positive")
        self._policy = policy
        self._loop = ref(asyncio.get_running_loop())
        self._capacity = capacity
        self._batch_size = min(batch_size, capacity)
        self._max_pending_bytes = max_pending_bytes
        self._pending: deque[_Validation] = deque()
        self._active = 0
        self._pending_bytes = 0
        self._worker: asyncio.Task[None] | None = None

    @property
    def idle(self) -> bool:
        """Return whether all accepted checks have finished and the worker exited."""
        return self._worker is None

    async def validate(self, leaf: bytes, chain: bytes) -> None:
        """Await this caller's result without cancelling other accepted checks."""
        loop = asyncio.get_running_loop()
        if loop is not self._loop():
            raise RuntimeError("Peer validator belongs to another event loop")
        if len(leaf) > 65_536 or len(chain) > 1_048_576:
            raise ValueError("Peer certificate exceeds bound")
        size = len(leaf) + len(chain)
        if (
            self._active >= self._capacity
            or self._pending_bytes + size > self._max_pending_bytes
        ):
            raise PeerValidationCapacityError("Peer validation capacity exceeded")
        result: asyncio.Future[None] = loop.create_future()
        self._pending.append(_Validation(_Certificate(leaf, chain), result))
        self._active += 1
        self._pending_bytes += size
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())
        await result

    def _finish(self, request: _Validation, error: BaseException | None) -> None:
        self._active -= 1
        self._pending_bytes -= len(request.certificate.leaf) + len(
            request.certificate.chain
        )
        if request.result.done():
            return
        if isinstance(error, asyncio.CancelledError):
            request.result.cancel()
        elif error is not None:
            request.result.set_exception(error)
        else:
            request.result.set_result(None)

    async def _run(self) -> None:
        try:
            while self._pending:
                # No later arrival can join after this fixed snapshot is taken.
                batch = tuple(
                    self._pending.popleft()
                    for _ in range(min(self._batch_size, len(self._pending)))
                )

                def validate_snapshot(
                    snapshot: tuple[_Validation, ...] = batch,
                ) -> dict[_Certificate, Exception | None]:
                    results: dict[_Certificate, Exception | None] = {}
                    for request in snapshot:
                        certificate = request.certificate
                        if certificate in results:
                            continue
                        try:
                            self._policy.validate(certificate.leaf, certificate.chain)
                        except Exception as error:
                            results[certificate] = error
                        else:
                            results[certificate] = None
                    return results

                try:
                    results = await asyncio.to_thread(validate_snapshot)
                except BaseException as error:
                    for request in batch:
                        self._finish(request, error)
                    while self._pending:
                        self._finish(self._pending.popleft(), error)
                    raise
                for request in batch:
                    self._finish(request, results[request.certificate])
        finally:
            self._worker = None
