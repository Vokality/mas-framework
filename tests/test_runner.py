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


class FakeService:
    """Stub MAS service for runner lifecycle tests."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class FakeGateway:
    """Stub gateway service for runner lifecycle tests."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _write_agents_yaml(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "agents:",
                "  - agent_id: test_agent",
                "    class_path: tests.test_runner:NoopAgent",
                "    instances: 1",
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
            )
        ]
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
                init_kwargs={"agent_id": "override"},
            )
        ]
    )
    runner = AgentRunner(settings)

    with pytest.raises(ValueError, match="reserved keys"):
        await runner._start_agents()


@pytest.mark.asyncio
async def test_runner_starts_and_stops_service(monkeypatch) -> None:
    monkeypatch.setattr("mas.runner.MASService", FakeService)
    monkeypatch.setattr("mas.runner.GatewayService", FakeGateway)
    settings = load_runner_settings(
        agents=[
            AgentSpec(
                agent_id="noop",
                class_path="tests.test_runner:NoopAgent",
            )
        ],
        service_redis_url="redis://example:6379",
    )
    runner = AgentRunner(settings)

    await runner._start_service()
    assert runner._service is not None
    assert runner._service.redis_url == "redis://example:6379"
    assert runner._service.started is True

    await runner._stop_service()
    assert runner._service is None


@pytest.mark.asyncio
async def test_runner_starts_and_stops_gateway(monkeypatch) -> None:
    monkeypatch.setattr("mas.runner.GatewayService", FakeGateway)
    settings = load_runner_settings(
        agents=[
            AgentSpec(
                agent_id="noop",
                class_path="tests.test_runner:NoopAgent",
            )
        ]
    )
    runner = AgentRunner(settings)

    await runner._start_gateway()
    assert runner._gateway is not None
    assert runner._gateway.started is True

    await runner._stop_gateway()
    assert runner._gateway is None


@pytest.mark.asyncio
async def test_runner_rejects_use_gateway_kwarg() -> None:
    settings = load_runner_settings(
        agents=[
            AgentSpec(
                agent_id="noop",
                class_path="tests.test_runner:NoopAgent",
                init_kwargs={"use_gateway": True},
            )
        ]
    )
    runner = AgentRunner(settings)

    with pytest.raises(ValueError, match="use_gateway is not supported"):
        await runner._start_agents()


@pytest.mark.asyncio
async def test_runner_requires_gateway_service() -> None:
    settings = load_runner_settings(
        agents=[
            AgentSpec(
                agent_id="noop",
                class_path="tests.test_runner:NoopAgent",
            )
        ],
        start_gateway=False,
    )
    runner = AgentRunner(settings)

    with pytest.raises(RuntimeError, match="start_gateway must be true"):
        await runner.run()
