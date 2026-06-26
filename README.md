# MAS Framework

Secure Python multi-agent runtime packages:
- Agents connect over gRPC + mTLS.
- The server owns Redis access for routing, state, audit, and policy.
- Agents never receive Redis credentials.

## Packages

- `mas-proto`: protobuf contract and generated gRPC bindings.
- `mas-core`: shared envelope, JSON, Redis, logging, and telemetry primitives.
- `mas-gateway`: authorization, DLP, rate limiting, circuit breaker, audit, and gateway configuration.
- `mas-server`: gRPC MAS server runtime.
- `mas-agent`: client runtime for agent processes.

The repository does not currently include bundled application entrypoints or an ops UI/API. Consumers compose `MASServer`, `GatewaySettings`, and `Agent` from their own application code.

## Minimal Shape

```python
from mas_gateway import GatewaySettings
from mas_server import AgentDefinition, MASServer, MASServerSettings, TlsConfig

agents = {
    "sender": AgentDefinition(agent_id="sender", capabilities=[], metadata={}),
    "worker": AgentDefinition(agent_id="worker", capabilities=[], metadata={}),
}

server = MASServer(
    settings=MASServerSettings(
        listen_addr="127.0.0.1:50051",
        tls=TlsConfig(
            server_cert_path="certs/server.pem",
            server_key_path="certs/server.key",
            client_ca_path="certs/ca.pem",
        ),
        agents=agents,
    ),
    gateway=GatewaySettings(),
)

await server.start()
await server.authz.set_permissions("sender", allowed_targets=["worker"])
```

Agent processes use `mas_agent.Agent` with `TlsClientConfig` and connect to the server over mTLS.

## Local Dependencies

Start Redis for local development:

```bash
docker compose up -d redis
```

## Development

```bash
uv sync --all-groups --all-packages
uv run ruff check .
uv run ruff format .
uv run pytest
uv run ty check
```
