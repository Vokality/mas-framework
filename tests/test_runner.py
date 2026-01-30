"""Tests for the config-driven agent runner."""

from __future__ import annotations

from pathlib import Path
from typing import cast, override

import pytest

from mas import Agent
from mas.runner import AgentRunner, AgentSpec, RunnerSettings


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


def _write_mas_yaml(path: Path, *, gateway_url: str | None = None) -> None:
    lines = [
        "tls_ca_path: tests/certs/ca.pem",
        "tls_server_cert_path: tests/certs/server.pem",
        "tls_server_key_path: tests/certs/server.key",
    ]
    if gateway_url is not None:
        lines.extend(
            [
                "gateway:",
                "  redis:",
                f"    url: {gateway_url}",
            ]
        )
    lines.extend(
        [
            "agents:",
            "  - agent_id: test_agent",
            "    class_path: tests.test_runner:NoopAgent",
            "    instances: 1",
            "    tls_cert_path: tests/certs/sender.pem",
            "    tls_key_path: tests/certs/sender.key",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_settings_loads_mas_yaml_from_parent(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "mas.yaml"
    _write_mas_yaml(config_path)

    nested = project_root / "apps" / "worker"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    settings = RunnerSettings()
    assert settings.config_file == str(config_path)
    assert len(settings.agents) == 1
    assert settings.agents[0].agent_id == "test_agent"


def test_settings_requires_mas_yaml(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    with pytest.raises(FileNotFoundError, match="mas.yaml not found"):
        RunnerSettings()


def test_settings_loads_gateway_from_mas_yaml(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "mas.yaml"
    _write_mas_yaml(config_path, gateway_url="redis://custom:6379")

    settings = RunnerSettings(config_file=str(config_path))
    assert settings.gateway["redis"]["url"] == "redis://custom:6379"


def test_settings_rejects_unknown_keys(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "mas.yaml"
    config_path.write_text(
        "\n".join(
            [
                "tls_ca_path: tests/certs/ca.pem",
                "tls_server_cert_path: tests/certs/server.pem",
                "tls_server_key_path: tests/certs/server.key",
                "unknown_key: true",
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

    with pytest.raises(ValueError, match="Unknown keys in mas.yaml"):
        RunnerSettings(config_file=str(config_path))


def test_load_agent_class_validation() -> None:
    with pytest.raises(ValueError, match="module:ClassName"):
        AgentRunner._load_agent_class("tests.test_runner.NoopAgent")

    with pytest.raises(TypeError, match="mas.Agent subclass"):
        AgentRunner._load_agent_class("tests.test_runner:NotAnAgent")

    loaded = AgentRunner._load_agent_class("tests.test_runner:NoopAgent")
    assert loaded is NoopAgent


@pytest.mark.asyncio
async def test_runner_start_respects_instances() -> None:
    settings = RunnerSettings(
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
    settings = RunnerSettings(
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
    settings = RunnerSettings(
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
    server = cast(FakeServer, runner._server)
    assert server.started is True

    await runner._stop_server()
    assert runner._server is None


@pytest.mark.asyncio
async def test_runner_passes_gateway_redis_to_server(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("mas.runner.MASServer", FakeServer)
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "mas.yaml"
    _write_mas_yaml(config_path, gateway_url="redis://custom:6380")

    settings = RunnerSettings(config_file=str(config_path))
    runner = AgentRunner(settings)

    await runner._start_server()
    assert runner._server is not None
    server = cast(FakeServer, runner._server)
    assert server.settings.redis_url == "redis://custom:6380"
    assert server.gateway.redis.url == "redis://custom:6380"
