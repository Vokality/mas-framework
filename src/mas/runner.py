"""Config-driven agent runner."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, TypedDict, Union, cast

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import Agent, TlsClientConfig
from .gateway.config import GatewaySettings
from .gateway.config import load_settings as load_gateway_settings
from .server import AgentDefinition, MASServer, MASServerSettings, TlsConfig

logger = logging.getLogger(__name__)


class AgentSpec(BaseModel):
    """Configuration for a single agent definition."""

    agent_id: str = Field(..., min_length=1, description="Agent ID to register")
    class_path: str = Field(
        ...,
        description="Import path for the agent class (module:ClassName)",
    )
    instances: int = Field(default=1, ge=1, description="Number of instances to run")
    capabilities: list[str] = Field(
        default_factory=list, description="Capabilities advertised by this agent"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Metadata advertised by this agent"
    )
    tls_cert_path: str = Field(..., description="Client certificate PEM path")
    tls_key_path: str = Field(..., description="Client private key PEM path")
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


class _RunnerSettingsInit(TypedDict, total=False):
    config_file: Optional[str]
    redis_url: str
    server_listen_addr: str
    tls_ca_path: str
    tls_server_cert_path: str
    tls_server_key_path: str
    gateway_config_file: Optional[str]
    permissions: list[PermissionSpec]
    agents: list[AgentSpec]


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
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis URL for MAS server",
    )
    server_listen_addr: str = Field(
        default="127.0.0.1:50051",
        description="gRPC listen address for MAS server",
    )
    tls_ca_path: str = Field(..., description="CA PEM used to verify peer certs")
    tls_server_cert_path: str = Field(..., description="Server certificate PEM")
    tls_server_key_path: str = Field(..., description="Server private key PEM")
    gateway_config_file: Optional[str] = Field(
        default=None, description="Path to gateway YAML config file"
    )
    permissions: list[PermissionSpec] = Field(
        default_factory=list,
        description="Authorization rules to apply",
    )
    agents: list[AgentSpec] = Field(
        default_factory=list,
        description="Agent definitions to run",
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
            merged_data: dict[str, Any] = {
                **yaml_data,
                **data,
                "config_file": config_file,
            }
            super().__init__(**cast(_RunnerSettingsInit, merged_data))
            self._resolve_config_paths()
        else:
            super().__init__(**cast(_RunnerSettingsInit, data))

    def _resolve_config_paths(self) -> None:
        if not self.config_file:
            return

        base = Path(self.config_file).resolve().parent

        def resolve_path(value: str) -> str:
            path = Path(value)
            if path.is_absolute():
                return value
            return str((base / path).resolve())

        self.tls_ca_path = resolve_path(self.tls_ca_path)
        self.tls_server_cert_path = resolve_path(self.tls_server_cert_path)
        self.tls_server_key_path = resolve_path(self.tls_server_key_path)

        if self.gateway_config_file:
            self.gateway_config_file = resolve_path(self.gateway_config_file)

        self.agents = [
            spec.model_copy(
                update={
                    "tls_cert_path": resolve_path(spec.tls_cert_path),
                    "tls_key_path": resolve_path(spec.tls_key_path),
                }
            )
            for spec in self.agents
        ]

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
        self._server: MASServer | None = None
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
            raise RuntimeError("No agents configured. Provide agents.yaml or settings.")

        self._setup_signals()
        try:
            await self._start_server()
            await self._apply_permissions()
            await self._start_agents()
            logger.info(
                "Runner started",
                extra={"agent_definitions": len(self._settings.agents)},
            )
            await self._shutdown_event.wait()
        finally:
            await self._stop_agents()
            await self._stop_server()

    def request_shutdown(self) -> None:
        """Signal the runner to shutdown."""
        if not self._shutdown_event.is_set():
            logger.info("Shutdown requested")
            self._shutdown_event.set()

    async def _start_agents(self) -> None:
        server_addr = (
            self._server.bound_addr
            if self._server is not None
            else self._settings.server_listen_addr
        )
        for spec in self._settings.agents:
            agent_cls = self._load_agent_class(spec.class_path)
            reserved_keys = {"agent_id", "server_addr", "tls"}
            conflicting = reserved_keys.intersection(spec.init_kwargs.keys())
            if conflicting:
                raise ValueError(
                    "init_kwargs contains reserved keys: "
                    + ", ".join(sorted(conflicting))
                )

            tls = TlsClientConfig(
                root_ca_path=self._settings.tls_ca_path,
                client_cert_path=spec.tls_cert_path,
                client_key_path=spec.tls_key_path,
            )
            for _ in range(spec.instances):
                agent = agent_cls(
                    spec.agent_id,
                    server_addr=server_addr,
                    tls=tls,
                    **spec.init_kwargs,
                )
                self._agents.append(agent)

        for agent in self._agents:
            await agent.start()

    async def _start_server(self) -> None:
        gateway_settings = self._load_gateway_settings()
        agents: dict[str, AgentDefinition] = {}
        for spec in self._settings.agents:
            agents[spec.agent_id] = AgentDefinition(
                agent_id=spec.agent_id,
                capabilities=list(spec.capabilities),
                metadata=dict(spec.metadata),
            )

        server_settings = MASServerSettings(
            redis_url=self._settings.redis_url,
            listen_addr=self._settings.server_listen_addr,
            tls=TlsConfig(
                server_cert_path=self._settings.tls_server_cert_path,
                server_key_path=self._settings.tls_server_key_path,
                client_ca_path=self._settings.tls_ca_path,
            ),
            agents=agents,
        )

        server = MASServer(settings=server_settings, gateway=gateway_settings)
        await server.start()
        self._server = server

    async def _stop_agents(self) -> None:
        if not self._agents:
            return

        await asyncio.gather(
            *(agent.stop() for agent in self._agents),
            return_exceptions=True,
        )
        self._agents.clear()

    async def _stop_server(self) -> None:
        if self._server is None:
            return

        await self._server.stop()
        self._server = None

    async def _apply_permissions(self) -> None:
        if not self._server or not self._settings.permissions:
            return

        authz = self._server.authz

        for spec in self._settings.permissions:
            if isinstance(spec, AllowBidirectionalSpec):
                await authz.add_permission(spec.agents[0], spec.agents[1])
                await authz.add_permission(spec.agents[1], spec.agents[0])
            elif isinstance(spec, AllowNetworkSpec):
                if spec.bidirectional:
                    for sender in spec.agents:
                        targets = [a for a in spec.agents if a != sender]
                        if targets:
                            await authz.set_permissions(sender, allowed_targets=targets)
                else:
                    for i in range(len(spec.agents) - 1):
                        await authz.add_permission(spec.agents[i], spec.agents[i + 1])
            elif isinstance(spec, AllowBroadcastSpec):
                await authz.set_permissions(spec.sender, allowed_targets=spec.receivers)
            elif isinstance(spec, AllowWildcardSpec):
                await authz.set_permissions(spec.agent_id, allowed_targets=["*"])
            elif isinstance(spec, AllowSpec):
                for target in spec.targets:
                    await authz.add_permission(spec.sender, target)

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
