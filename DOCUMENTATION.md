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

## Server Configuration

`MASServerSettings` fields beyond the required `listen_addr`, `tls`, and `agents`
allowlist:

- `max_in_flight` (default `200`): per-session cap on undelivered stream entries.
- `reclaim_idle_ms` (default `30000`): idle time before a pending stream entry
  can be reclaimed by another consumer.
- `reclaim_batch_size` (default `50`): maximum entries reclaimed per pass.

## Local Development Bootstrap

For local development and tests, `mas_server.dev` provides helpers that generate
loopback-only mTLS material (requires the `openssl` CLI) and start a broker with
`GatewaySettings()` defaults:

```python
from pathlib import Path

from mas_agent import Agent, TlsClientConfig
from mas_server import AgentDefinition
from mas_server.dev import client_tls, start_dev_server

agents = {
    "router": AgentDefinition(agent_id="router", capabilities=[], metadata={}),
    "helpdesk": AgentDefinition(agent_id="helpdesk", capabilities=["qa"], metadata={}),
}

cert_dir = Path(".mas-dev-certs")
server, tls = await start_dev_server(
    agents=agents,
    cert_dir=cert_dir,
    mesh=True,
)
try:
    creds = client_tls(tls, "router")
    router = Agent(
        "router",
        server_addr=server.bound_addr,
        tls=TlsClientConfig(
            root_ca_path=creds.root_ca_path,
            client_cert_path=creds.client_cert_path,
            client_key_path=creds.client_key_path,
        ),
    )
    await router.start()
finally:
    await server.stop()
```

- `cert_dir` is required; client certificates are generated eagerly for every
  allowlisted agent id (override with `agent_ids=` when you need extras).
- `mesh=False` (default) preserves deny-by-default ACLs.
- `mesh=True` is a dev-only shortcut that allows each allowlisted agent to
  message every other allowlisted agent (never itself).
- Dev certificates cover `127.0.0.1` and `localhost` listen addresses only.
  Use an explicit production `TlsConfig` outside local development.

## Agent API

Core messaging:
- `await agent.send(target_id, message_type, data)`: fire-and-forget delivery to another agent.
- `reply = await agent.request(target_id, message_type, data, timeout=...)`: request/reply pattern; waits for a response or timeout. When `timeout` is omitted, the client and server both use a 60-second budget. Passing `timeout <= 0` raises `ValueError`.
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

On NACK, the server requeues retryable deliveries before XACKing the stream
entry, and only XACKs non-retryable deliveries after a successful DLQ write. If
requeue or DLQ write fails, the entry stays pending in Redis for redelivery.

## Request/Reply Correlation

1. The server stores request origin metadata in `mas.pending_request:{correlation_id}` with a TTL derived from the request timeout.
2. If policy routing fails after the pending key is written, the server deletes the key before returning the error.
3. A reply must come from the agent that received the original request; the server rejects mismatched senders.
4. The server atomically reserves a pending key (delete-if-unchanged) before routing the reply, so concurrent duplicate replies cannot both succeed.
5. The requesting client binds each pending call to its target agent and ignores replies from any other sender.

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
