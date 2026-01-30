# MAS Framework Documentation

## Running

The canonical entrypoint is the runner:

```bash
uv run python -m mas
```

The runner searches upward from the current working directory for `mas.yaml`.

If a config file is used (auto-discovered or passed via `MAS_RUNNER_CONFIG_FILE`), all relative paths inside it (TLS cert/key paths) are resolved relative to that config file.

## Configuration (`mas.yaml`)

See `mas.yaml.example`.

Key points:
- mTLS is required (server cert/key + CA, and a client cert/key per agent)
- Client certificates must contain a SPIFFE URI SAN: `spiffe://mas/agent/{agent_id}`
- Agents are allowlisted by `agents:` in the config
- Authorization is deny-by-default; configure `permissions:` explicitly

Common fields:
- `server_listen_addr`: gRPC bind address for the MAS server.
- `tls_ca_path`, `tls_server_cert_path`, `tls_server_key_path`: server mTLS credentials.
- `permissions`: allow/deny rules for agent-to-agent traffic.
- `agents`: list of agent entries; each includes `agent_id`, `class_path`, `instances`, `capabilities`, `metadata`, `tls_cert_path`, `tls_key_path`, and `init_kwargs`.
- `gateway`: optional gateway policy settings (rate limits, DLP, RBAC, circuit breaker).

## Agent API

Core messaging:
- `await agent.send(target_id, message_type, data)`: fire-and-forget delivery to another agent.
- `reply = await agent.request(target_id, message_type, data, timeout=...)`: request/reply pattern; waits for a response or timeout.
- `await message.reply(message_type, data)`: reply to an incoming message; preserves correlation for the original request.

Discovery:
- `agents = await agent.discover(capabilities=[...])`: find active agents by capability tags.

State:
- `await agent.update_state({...})`: persist per-agent state via the server.
- `await agent.refresh_state()`: reload state from the server.
- `await agent.reset_state()`: clear state back to model defaults.

Handlers:
- `@Agent.on("type", model=...)`: register a typed handler for an incoming `message_type`.
- `async def on_message(self, message)`: fallback for untyped/unhandled messages.

## Writing Agent Classes

Runner injects `server_addr` and `tls` into your constructor. Accept `**kwargs` and pass through:

```python
from mas import Agent


class MyAgent(Agent[dict[str, object]]):
    def __init__(self, agent_id: str, **kwargs: object) -> None:
        super().__init__(agent_id, **kwargs)

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

## Gateway Policy (`mas.yaml`)

Gateway settings live under the top-level `gateway:` key in `mas.yaml`.

Supported settings:
- `redis`: connection parameters used by the server.
- `rate_limit`: per-agent message limits per minute/hour.
- `features`: toggles for DLP, RBAC, circuit breaker.
- `dlp`: custom DLP rules and policy overrides.
- `audit`: optional JSONL audit file sink settings.
- `circuit_breaker`: failure/success thresholds and timeout window.

## End-to-end Example

See `examples/e2e_ping_pong/`.
