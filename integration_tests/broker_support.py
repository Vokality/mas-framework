"""Isolated broker processes started through the operator-facing CLI."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from mas_gateway.config import GatewaySettings
from mas_server.cli import BrokerReady
from mas_server.types import MASServerSettings
from pydantic import TypeAdapter


@dataclass(slots=True)
class BrokerProcess:
    """Own one real broker and bounded, graceful telemetry shutdown."""

    directory: Path
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _log: BinaryIO | None = field(default=None, repr=False)

    async def start(
        self, settings: MASServerSettings, gateway: GatewaySettings
    ) -> BrokerReady:
        """Validate the CLI readiness boundary before returning an endpoint."""
        if self.process is not None:
            raise RuntimeError("broker process has already been started")
        self.directory = self.directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        settings_path = self.directory / "server.json"
        settings_path.write_bytes(TypeAdapter(MASServerSettings).dump_json(settings))
        gateway_path = self.directory / "gateway.json"
        gateway_path.write_text(gateway.model_dump_json(exclude={"config_file"}))
        self._log = (self.directory / "broker.log").open("wb")
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "mas_server",
                "--settings",
                str(settings_path),
                "--gateway",
                str(gateway_path),
                cwd=self.directory,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if not key.upper().startswith("GATEWAY_")
                },
                stdout=asyncio.subprocess.PIPE,
                stderr=self._log,
            )
            stdout = self.process.stdout
            assert stdout is not None
            async with asyncio.timeout(15):
                line = await stdout.readline()
            if not line:
                raise RuntimeError(f"broker exited before readiness: {self.directory}")
            ready = BrokerReady.model_validate_json(line)
            if ready.pid != self.process.pid:
                raise RuntimeError("broker readiness PID does not match owned process")
            return ready
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> int | None:
        """Drain the child, falling back to bounded forced termination."""
        process = self.process
        try:
            if process is None:
                return None
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    async with asyncio.timeout(20):
                        return await process.wait()
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    async with asyncio.timeout(5):
                        return await process.wait()
            return process.returncode
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None
