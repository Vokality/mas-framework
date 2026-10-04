# MAS Framework Documentation

## Runtime Composition

After editing the wire contract, regenerate it with
`uv run python -m tools.generate_proto`. This uses locked code generators and
adds the explicit experimental gRPC submodule import required by generated calls.

This repository provides library packages and a supervisor-friendly broker
entrypoint: `uv run python -m mas_server --settings broker.json --gateway gateway.yaml`.
An embedding host application is responsible for:

- Building `AgentDefinition` entries for allowlisted agents.
- Constructing `MASServerSettings` and `GatewaySettings`.
- Starting and stopping `MASServer`.
- Starting agent processes with `server_addr` and `TlsClientConfig`.
- Applying authorization rules through `AuthorizationModule`.

## Gateway Configuration

Gateway policy can be configured with `GatewaySettings`, environment variables, or a standalone YAML file loaded with `GatewaySettings.from_yaml(...)`.

Supported settings:
- `redis`: standalone URL or Sentinel discovery, TLS credentials and explicit replica/AOF confirmation policy.
- `rate_limit`: per-agent message limits per minute/hour.
- `features`: toggles for DLP, RBAC, and circuit breaker.
- `dlp`: custom DLP rules and policy overrides.
- `audit`: optional JSONL sink, bounded retained records and durable archive settings.
- `telemetry`: OpenTelemetry exporter settings.
- `circuit_breaker`: failure/success thresholds and timeout window.

## Server Configuration

`MASServerSettings` fields beyond the required `listen_addr`, `tls`, and `agents`
allowlist:

- `max_in_flight` (default `200`): per-session cap on deliveries awaiting ACK/NACK.
- `max_delivery_attempts` (default `5`): maximum handler attempts before DLQ.
- `management` (default `None`): optional `mas_server.management.ManagementSettings`
  for the bundled dashboard, `/api/snapshot`, and `/healthz`.
- `session_lease`: shared ownership TTL and heartbeat interval (6s and 2s defaults).
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

State updates use the revision returned by the latest read or successful write.
Concurrent writers receive gRPC `ABORTED` with `state_revision_conflict`; refresh
and recompute the transition before retrying. This changes the wire contract:
upgrade broker and agent packages together. An omitted expected revision is
rejected rather than interpreted as an unconditional write.

Handlers:
- `@Agent.on("type", model=...)`: register a typed handler for an incoming `message_type`.
- `async def on_message(self, message)`: fallback for untyped or unhandled messages.

Delivery is at-least-once. Handler code that performs side effects must be
idempotent or deduplicate by `message.message_id`, because Redis stream reclaim
can redeliver work when ACKs are delayed or a client disconnects.

On NACK, the server atomically requeues retryable deliveries before XACKing the stream
entry, and only XACKs non-retryable deliveries after a successful DLQ write. If
requeue or DLQ write fails, the entry stays pending in Redis for redelivery.

## Request/Reply Correlation

1. The server stores request origin metadata in `mas.pending_request:{correlation_id}` with a TTL derived from the request timeout.
2. A confirmed policy rejection removes the pending key. An uncertain commit preserves its bounded TTL because the request may already be queued.
3. A reply must come from the agent that received the original request; the server rejects mismatched senders.
4. One atomic commit enqueues the reply, stores its receipt, and consumes the unchanged pending key. An identical retry returns the original message ID until the original deadline; changed reply content is rejected.
5. The requesting client binds each pending call to its target agent and ignores replies from any other sender. Normal accepted sends atomically append their audit record and queue entry, then wait for configured persistence confirmation.

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

    async def on_start(self) -> None: ...

    async def on_stop(self) -> None: ...

    async def on_message(self, message: AgentMessage) -> None: ...
```

Lifecycle hooks:
- `on_start`: runs after transport is ready and state is loaded.
- `on_stop`: runs during shutdown before the transport task is torn down.
- `on_message`: fallback for messages with no registered handler.

Acknowledged stream entries are deleted atomically with XACK after verifying the
consumer still owns the pending entry. Repeated handler errors are preserved in
the DLQ after `max_delivery_attempts`. Audit streams retain the policy history.
See README.md for dashboard configuration and the runnable control-room example.
