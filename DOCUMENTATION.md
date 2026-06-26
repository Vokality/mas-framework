# MAS Framework Documentation

## Runtime Composition

This repository provides library packages. A host application is responsible for:

- Building `AgentDefinition` entries for allowlisted agents.
- Constructing `MASServerSettings` and `GatewaySettings`.
- Starting and stopping `MASServer`.
- Starting agent processes with `server_addr` and `TlsClientConfig`.
- Applying authorization rules through `AuthorizationModule`.

## Gateway Configuration

Gateway policy can be configured with `GatewaySettings`, environment variables, or a standalone YAML file loaded with `GatewaySettings.from_yaml(...)`.

Supported settings:
- `redis`: connection parameters used by the server.
- `rate_limit`: per-agent message limits per minute/hour.
- `features`: toggles for DLP, RBAC, and circuit breaker.
- `dlp`: custom DLP rules and policy overrides.
- `audit`: optional JSONL audit file sink settings.
- `telemetry`: OpenTelemetry exporter settings.
- `circuit_breaker`: failure/success thresholds and timeout window.

## Agent API

Core messaging:
- `await agent.send(target_id, message_type, data)`: fire-and-forget delivery to another agent.
- `reply = await agent.request(target_id, message_type, data, timeout=...)`: request/reply pattern; waits for a response or timeout.
- `await agent.send_reply_envelope(message, message_type, data)`: reply to an incoming request while preserving correlation metadata.

Discovery:
- `agents = await agent.discover(capabilities=[...])`: find active agents by capability tags.

State:
- `await agent.update_state({...})`: persist per-agent state via the server.
- `await agent.refresh_state()`: reload state from the server.
- `await agent.reset_state()`: clear state back to model defaults.

Handlers:
- `@Agent.on("type", model=...)`: register a typed handler for an incoming `message_type`.
- `async def on_message(self, message)`: fallback for untyped or unhandled messages.

Delivery is at-least-once. Handler code that performs side effects must be
idempotent or deduplicate by `message.message_id`, because Redis stream reclaim
can redeliver work when ACKs are delayed or a client disconnects.

## Writing Agent Classes

Accept `server_addr` and `tls` in the constructor and pass them through to `Agent`.

```python
from mas_agent import Agent, AgentMessage, TlsClientConfig


class MyAgent(Agent):
    def __init__(
        self,
        agent_id: str,
        *,
        server_addr: str = "localhost:50051",
        tls: TlsClientConfig | None = None,
    ) -> None:
        super().__init__(agent_id, server_addr=server_addr, tls=tls)

    async def on_start(self) -> None:
        ...

    async def on_stop(self) -> None:
        ...

    async def on_message(self, message: AgentMessage) -> None:
        ...
```

Lifecycle hooks:
- `on_start`: runs after transport is ready and state is loaded.
- `on_stop`: runs during shutdown before the transport task is torn down.
- `on_message`: fallback for messages with no registered handler.
