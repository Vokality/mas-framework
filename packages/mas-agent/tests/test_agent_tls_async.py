"""TLS credential I/O must not block agent lifecycle orchestration."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import BinaryIO

import grpc
import grpc.aio as grpc_aio
import pytest
from mas_agent import agent as agent_module
from mas_agent.agent import Agent
from mas_agent.config import TlsClientConfig


class _ReadyAgent(Agent):
    async def _transport_loop(self) -> None:
        self._transport_ready.set()
        await asyncio.Event().wait()

    async def _load_state(self) -> None:
        return


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_credential_reads_yield_and_cancel_without_opening_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    paths = tuple(tmp_path / name for name in ("ca", "certificate", "key"))
    for path in paths:
        path.write_bytes(path.name.encode())
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    loaded: list[tuple[bytes | None, bytes | None, bytes | None]] = []
    original_credentials = grpc.ssl_channel_credentials

    def read_credentials(path: str, mode: str) -> BinaryIO:
        assert mode == "rb"
        worker_threads.append(threading.get_ident())
        if path == str(paths[0]):
            entered.set()
            assert release.wait(1), "credential reader was not released"
        return open(path, "rb")

    def credentials(
        root_certificates: bytes | None = None,
        private_key: bytes | None = None,
        certificate_chain: bytes | None = None,
    ) -> grpc.ChannelCredentials:
        loaded.append((root_certificates, private_key, certificate_chain))
        result = original_credentials()
        finished.set()
        return result

    def channel(target: str, credentials: grpc.ChannelCredentials) -> grpc_aio.Channel:
        del credentials
        assert threading.get_ident() == loop_thread
        return grpc_aio.insecure_channel(target)

    monkeypatch.setattr(agent_module, "open", read_credentials, raising=False)
    monkeypatch.setattr(grpc, "ssl_channel_credentials", credentials)
    monkeypatch.setattr(grpc_aio, "secure_channel", channel)
    agent = _ReadyAgent("worker", tls=TlsClientConfig(*(str(path) for path in paths)))
    task = asyncio.create_task(agent.start())
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        await asyncio.sleep(0.01)
        assert not task.done()
        assert agent._channel is None
        assert worker_threads == [worker_threads[0]]
        assert worker_threads[0] != loop_thread
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.2)
            assert agent._transport_task is None
            assert not agent._running
        release.set()
        assert await asyncio.to_thread(finished.wait, 0.5)
        assert loaded == [(b"ca", b"key", b"certificate")]
        if not cancel:
            await task
            await agent.stop()
            for path in paths:
                path.write_bytes(b"rotated " + path.name.encode())
            await agent.start()
            assert loaded == [
                (b"ca", b"key", b"certificate"),
                (b"rotated ca", b"rotated key", b"rotated certificate"),
            ]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 0.5)
        await agent.stop()


@pytest.mark.asyncio
async def test_failed_credentials_leave_agent_restartable(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.pem")
    agent = Agent("worker", tls=TlsClientConfig(missing, missing, missing))
    for _ in range(2):
        with pytest.raises(FileNotFoundError):
            await agent.start()
        assert agent._channel is None
        assert agent._transport_task is None
        assert not agent._running
