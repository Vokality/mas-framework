"""Config-driven agent runner."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union, cast

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import Agent
from .gateway import GatewayService
from .gateway.config import GatewaySettings, load_settings as load_gateway_settings
from .service import MASService

logger = logging.getLogger(__name__)


class AgentSpec(BaseModel):
    """Configuration for a single agent definition."""

    agent_id: str = Field(..., min_length=1, description="Agent ID to register")
    class_path: str = Field(
        ...,
        description="Import path for the agent class (module:ClassName)",
    )
    instances: int = Field(
        default=1, ge=1, description="Number of instances to run"
    )
    init_kwargs: dict[str, Any] = Field(
        default_factory=dict, description="Kwargs forwarded to agent constructor"
    )


class AllowBidirectionalSpec(BaseModel):
    """Bidirectional permission for two agents."""

    type: Literal["allow_bidirectional"]
    agents: list[str] = Field(min_length=2, max_length=2)


class AllowNetworkSpec(BaseModel):
    """Full mesh or chained network permissions."""

    type: Literal["allow_network"]
    agents: list[str] = Field(min_length=2)
    bidirectional: bool = True


class AllowBroadcastSpec(BaseModel):
    """One-way broadcast permissions."""

    type: Literal["allow_broadcast"]
    sender: str
    receivers: list[str] = Field(min_length=1)


class AllowWildcardSpec(BaseModel):
    """Wildcard permission for a single agent."""

    type: Literal["allow_wildcard"]
    agent_id: str


class AllowSpec(BaseModel):
    """One-way permissions from a sender to targets."""

    type: Literal["allow"]
    sender: str
    targets: list[str] = Field(min_length=1)


PermissionSpec = Annotated[
    Union[
        AllowBidirectionalSpec,
        AllowNetworkSpec,
        AllowBroadcastSpec,
        AllowWildcardSpec,
        AllowSpec,
    ],
    Field(discriminator="type"),
]


class RunnerSettings(BaseSettings):
    """
    Runner configuration.

    Configuration sources:
    1) Explicit parameters
    2) Environment variables (MAS_RUNNER_*)
    3) agents.yaml (auto-loaded if present)
    4) Defaults
    """

    config_file: Optional[str] = Field(
        default=None, description="Path to YAML config file"
    )
    start_service: bool = Field(
        default=True, description="Start MAS service alongside agents"
    )
    service_redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis URL for MAS service",
    )
    start_gateway: bool = Field(
        default=True, description="Start gateway service for all agents"
    )
    gateway_config_file: Optional[str] = Field(
        default=None, description="Path to gateway YAML config file"
    )
    permissions: list[PermissionSpec] = Field(
        default_factory=list, description="Authorization rules to apply"
    )
    agents: list[AgentSpec] = Field(
        default_factory=list, description="Agent definitions to run"
    )

    model_config = SettingsConfigDict(
        env_prefix="MAS_RUNNER_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def __init__(self, **data: Any) -> None:
        config_file = (
            data.get("config_file")
            or os.getenv("MAS_RUNNER_CONFIG_FILE")
            or self._default_config_file()
        )

        if config_file is None and "agents" not in data:
            raise FileNotFoundError(
                "agents.yaml not found. Create agents.yaml in the project root "
                "or pass a config_file."
            )

        if config_file:
            yaml_data = self._load_yaml(config_file)
            merged_data = {**yaml_data, **data, "config_file": config_file}
            super().__init__(**merged_data)
        else:
            super().__init__(**data)

    @staticmethod
    def _default_config_file() -> Optional[str]:
        start = Path.cwd()
        for current in [start, *start.parents]:
            candidate = current / "agents.yaml"
            if candidate.exists():
                return str(candidate)
        return None

    @staticmethod
    def _load_yaml(file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        with path.open("r") as f:
            data = yaml.safe_load(f)

        if data is None:
            return {}

        return data


class AgentRunner:
    """Start and supervise agent instances from RunnerSettings."""

    def __init__(self, settings: RunnerSettings) -> None:
        self._settings = settings
        self._agents: list[Agent[Any]] = []
        self._service: MASService | None = None
        self._gateway: GatewayService | None = None
        self._shutdown_event = asyncio.Event()
        self._ensure_import_base()

    def _ensure_import_base(self) -> None:
        if not self._settings.config_file:
            return
        base = str(Path(self._settings.config_file).resolve().parent)
        if base not in sys.path:
            sys.path.insert(0, base)

    async def run(self) -> None:
        """Start agents and wait for shutdown."""
        if not self._settings.agents:
            raise RuntimeError(
                "No agents configured. Provide agents.yaml or settings."
            )
        if not self._settings.start_gateway:
            raise RuntimeError(
                "Gateway service is required. start_gateway must be true."
            )

        self._setup_signals()
        try:
            await self._start_service()
            await self._start_gateway()
            await self._apply_permissions()
            await self._start_agents()
            logger.info(
                "Runner started",
                extra={"agent_definitions": len(self._settings.agents)},
            )
            await self._shutdown_event.wait()
        finally:
            await self._stop_agents()
            await self._stop_gateway()
            await self._stop_service()

    def request_shutdown(self) -> None:
        """Signal the runner to shutdown."""
        if not self._shutdown_event.is_set():
            logger.info("Shutdown requested")
            self._shutdown_event.set()

    async def _start_agents(self) -> None:
        for spec in self._settings.agents:
            agent_cls = self._load_agent_class(spec.class_path)
            reserved_keys = {"agent_id"}
            conflicting = reserved_keys.intersection(spec.init_kwargs.keys())
            if conflicting:
                raise ValueError(
                    "init_kwargs contains reserved keys: "
                    + ", ".join(sorted(conflicting))
                )
            if "use_gateway" in spec.init_kwargs:
                raise ValueError(
                    "use_gateway is not supported. MAS always routes via the gateway."
                )
            for _ in range(spec.instances):
                agent = agent_cls(spec.agent_id, **spec.init_kwargs)
                self._agents.append(agent)

        for agent in self._agents:
            await agent.start()

    async def _start_service(self) -> None:
        if not self._settings.start_service:
            return

        service = MASService(redis_url=self._settings.service_redis_url)
        await service.start()
        self._service = service

    async def _start_gateway(self) -> None:
        if not self._settings.start_gateway:
            return

        settings = self._load_gateway_settings()
        gateway = GatewayService(settings=settings)
        await gateway.start()
        self._gateway = gateway

    async def _stop_agents(self) -> None:
        if not self._agents:
            return

        await asyncio.gather(
            *(agent.stop() for agent in self._agents),
            return_exceptions=True,
        )
        self._agents.clear()

    async def _stop_gateway(self) -> None:
        if self._gateway is None:
            return

        await self._gateway.stop()
        self._gateway = None

    async def _apply_permissions(self) -> None:
        if not self._gateway or not self._settings.permissions:
            return

        auth = self._gateway.auth_manager()
        pending_apply = False

        for spec in self._settings.permissions:
            if isinstance(spec, AllowBidirectionalSpec):
                await auth.allow_bidirectional(spec.agents[0], spec.agents[1])
            elif isinstance(spec, AllowNetworkSpec):
                await auth.allow_network(spec.agents, bidirectional=spec.bidirectional)
            elif isinstance(spec, AllowBroadcastSpec):
                await auth.allow_broadcast(spec.sender, spec.receivers)
            elif isinstance(spec, AllowWildcardSpec):
                await auth.allow_wildcard(spec.agent_id)
            elif isinstance(spec, AllowSpec):
                auth.allow(spec.sender, spec.targets)
                pending_apply = True

        if pending_apply:
            await auth.apply()

    async def _stop_service(self) -> None:
        if self._service is None:
            return

        await self._service.stop()
        self._service = None

    def _setup_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda *_: self.request_shutdown())

    @staticmethod
    def _load_agent_class(class_path: str) -> type[Agent[Any]]:
        if ":" not in class_path:
            raise ValueError(
                f"Invalid class_path '{class_path}'. Use module:ClassName format."
            )

        module_name, class_name = class_path.split(":", 1)
        module = importlib.import_module(module_name)
        target = getattr(module, class_name)

        if not isinstance(target, type):
            raise TypeError(f"{class_path} does not reference a class")

        if not issubclass(target, Agent):
            raise TypeError(f"{class_path} is not a mas.Agent subclass")

        return cast(type[Agent[Any]], target)

    def _load_gateway_settings(self) -> GatewaySettings:
        if self._settings.gateway_config_file:
            return load_gateway_settings(config_file=self._settings.gateway_config_file)

        if self._settings.config_file:
            base = Path(self._settings.config_file).resolve().parent
            candidate = base / "gateway.yaml"
            if candidate.exists():
                return load_gateway_settings(config_file=str(candidate))

        return load_gateway_settings()


def load_runner_settings(
    config_file: Optional[str] = None, **overrides: Any
) -> RunnerSettings:
    """Load runner settings with optional overrides."""
    if config_file:
        overrides["config_file"] = config_file
    return RunnerSettings(**overrides)


async def main(config_file: Optional[str] = None) -> None:
    """Run agents defined by RunnerSettings."""
    settings = load_runner_settings(config_file=config_file)
    runner = AgentRunner(settings)
    try:
        await runner.run()
    except RuntimeError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


def run(config_file: Optional[str] = None) -> None:
    """Sync entrypoint for the runner."""
    asyncio.run(main(config_file=config_file))


if __name__ == "__main__":
    run()
