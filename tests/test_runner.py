"""Tests for the config-driven agent runner."""

from __future__ import annotations

from pathlib import Path
from typing import override

import pytest

from mas import Agent
from mas.runner import AgentRunner, AgentSpec, load_runner_settings


class NoopAgent(Agent[dict[str, object]]):
    """Agent with no-op lifecycle for runner tests."""

    @override
    async def start(self) -> None:
        self._running = True

    @override
    async def stop(self) -> None:
        self._running = False


class NotAnAgent:
    """Dummy class for validation tests."""


class FakeServer:
    """Stub MAS server for runner lifecycle tests."""

    def __init__(self, *, settings, gateway) -> None:
        self.settings = settings
        self.gateway = gateway
        self.started = False
        self.stopped = False

    @property
    def authz(self):
        raise RuntimeError("not needed for this test")

    @property
    def bound_addr(self) -> str:
        return "127.0.0.1:50051"

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _write_agents_yaml(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "tls_ca_path: tests/certs/ca.pem",
                "tls_server_cert_path: tests/certs/server.pem",
                "tls_server_key_path: tests/certs/server.key",
                "agents:",
                "  - agent_id: test_agent",
                "    class_path: tests.test_runner:NoopAgent",
                "    instances: 1",
                "    tls_cert_path: tests/certs/sender.pem",
                "    tls_key_path: tests/certs/sender.key",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_settings_loads_agents_yaml_from_parent(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "agents.yaml"
    _write_agents_yaml(config_path)

    nested = project_root / "apps" / "worker"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    settings = load_runner_settings()
    assert settings.config_file == str(config_path)
    assert len(settings.agents) == 1
    assert settings.agents[0].agent_id == "test_agent"


def test_settings_requires_agents_yaml(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    with pytest.raises(FileNotFoundError, match="agents.yaml not found"):
        load_runner_settings()


def test_load_agent_class_validation() -> None:
    with pytest.raises(ValueError, match="module:ClassName"):
        AgentRunner._load_agent_class("tests.test_runner.NoopAgent")

    with pytest.raises(TypeError, match="mas.Agent subclass"):
        AgentRunner._load_agent_class("tests.test_runner:NotAnAgent")

    loaded = AgentRunner._load_agent_class("tests.test_runner:NoopAgent")
    assert loaded is NoopAgent


@pytest.mark.asyncio
async def test_runner_start_respects_instances() -> None:
    settings = load_runner_settings(
        agents=[
            AgentSpec(
                agent_id="noop",
                class_path="tests.test_runner:NoopAgent",
                instances=2,
                tls_cert_path="tests/certs/sender.pem",
                tls_key_path="tests/certs/sender.key",
            )
        ],
        tls_ca_path="tests/certs/ca.pem",
        tls_server_cert_path="tests/certs/server.pem",
        tls_server_key_path="tests/certs/server.key",
    )
    runner = AgentRunner(settings)

    await runner._start_agents()
    try:
        assert len(runner._agents) == 2
    finally:
        await runner._stop_agents()


@pytest.mark.asyncio
async def test_runner_rejects_reserved_kwargs() -> None:
    settings = load_runner_settings(
        agents=[
            AgentSpec(
                agent_id="noop",
                class_path="tests.test_runner:NoopAgent",
                tls_cert_path="tests/certs/sender.pem",
                tls_key_path="tests/certs/sender.key",
                init_kwargs={"agent_id": "override"},
            )
        ],
        tls_ca_path="tests/certs/ca.pem",
        tls_server_cert_path="tests/certs/server.pem",
        tls_server_key_path="tests/certs/server.key",
    )
    runner = AgentRunner(settings)

    with pytest.raises(ValueError, match="reserved keys"):
        await runner._start_agents()


@pytest.mark.asyncio
async def test_runner_starts_and_stops_server(monkeypatch) -> None:
    monkeypatch.setattr("mas.runner.MASServer", FakeServer)
    settings = load_runner_settings(
        agents=[
            AgentSpec(
                agent_id="noop",
                class_path="tests.test_runner:NoopAgent",
                tls_cert_path="tests/certs/sender.pem",
                tls_key_path="tests/certs/sender.key",
            )
        ],
        tls_ca_path="tests/certs/ca.pem",
        tls_server_cert_path="tests/certs/server.pem",
        tls_server_key_path="tests/certs/server.key",
    )
    runner = AgentRunner(settings)

    await runner._start_server()
    assert runner._server is not None
    assert runner._server.started is True

    await runner._stop_server()
    assert runner._server is None
