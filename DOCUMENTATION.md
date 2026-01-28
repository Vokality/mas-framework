# MAS Framework Documentation

## Running

The canonical entrypoint is the runner:

```bash
uv run python -m mas
```

The runner searches upward from the current working directory for `agents.yaml`.

If a config file is used (auto-discovered or passed via `MAS_RUNNER_CONFIG_FILE`), all relative paths inside it (TLS cert/key paths and `gateway_config_file`) are resolved relative to that config file.

## Configuration (`agents.yaml`)

See `agents.yaml.example`.

Key points:
- mTLS is required (server cert/key + CA, and a client cert/key per agent)
- Client certificates must contain a SPIFFE URI SAN: `spiffe://mas/agent/{agent_id}`
- Agents are allowlisted by `agents:` in the config
- Authorization is deny-by-default; configure `permissions:` explicitly

## Agent API

- `await agent.send(target_id, message_type, data)`
- `reply = await agent.request(target_id, message_type, data, timeout=...)`
- `await message.reply(message_type, data)`
- `agents = await agent.discover(capabilities=[...])`
- `await agent.update_state({...})`, `await agent.refresh_state()`, `await agent.reset_state()`

## Writing Agent Classes

Runner injects `server_addr` and `tls` into your constructor. Accept `**kwargs` and pass through:

```python
from mas import Agent


class MyAgent(Agent[dict[str, object]]):
    def __init__(self, agent_id: str, **kwargs: object) -> None:
        super().__init__(agent_id, **kwargs)
```

## Security Policy (`gateway.yaml`)

The server can load optional policy overrides from `gateway.yaml` (see `gateway.yaml.example`).

Supported settings:
- Redis connection
- Rate limits
- Feature flags: DLP, RBAC, circuit breaker
- Circuit breaker thresholds

## End-to-end Example

See `examples/e2e_ping_pong/`.
