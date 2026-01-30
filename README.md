# MAS Framework

Secure multi-agent runtime:
- Agents connect over gRPC + mTLS
- Server owns all Redis access (routing, state, audit, policy)
- Agents never touch Redis

## Use Cases

- Ship multi-agent systems without widening data access: enforce deny-by-default ACLs and mTLS identity per agent.
- Meet security and compliance requirements with tamper-evident audit logs and explicit policy decisions.
- Protect sensitive data in transit using DLP scanning with block/redact rules.
- Reduce incident blast radius with centralized policy, rate limiting, and circuit breakers.
- Support multi-team agent development without shared Redis credentials or direct data access.

## Architecture

```
Agent (mTLS)  ─┐
Agent (mTLS)  ─┼─→  MAS Server (gRPC + mTLS)
Agent (mTLS)  ─┘        ├─ AuthN: SPIFFE URI SAN (spiffe://mas/agent/{agent_id})
                         ├─ AuthZ: deny-by-default ACL (+ optional RBAC)
                         ├─ DLP scanning (block/redact)
                         ├─ Rate limiting
                         ├─ Circuit breaker + DLQ
                         ├─ Audit log (Redis Streams, tamper-evident)
                         └─ Redis Streams for durable delivery + Redis hashes for state
```

## Quick Start

1) Start Redis

```bash
redis-server
```

2) Create `mas.yaml`

Use `mas.yaml.example` as a template. You must provide:
- Server cert/key + CA (`tls_ca_path`, `tls_server_cert_path`, `tls_server_key_path`)
- A client cert/key per agent (`tls_cert_path`, `tls_key_path`)

3) Implement agents

Runner injects `server_addr` and `tls` into your agent constructor; accept `**kwargs` and pass through.

```python
from __future__ import annotations

from pydantic import BaseModel

from mas import Agent, AgentMessage


class Ping(BaseModel):
    value: int


class EchoAgent(Agent[dict[str, object]]):
    def __init__(self, agent_id: str, **kwargs: object) -> None:
        super().__init__(agent_id, **kwargs)

    @Agent.on("ping", model=Ping)
    async def handle_ping(self, message: AgentMessage, payload: Ping) -> None:
        await message.reply("pong", {"value": payload.value + 1})
```

4) Run

```bash
uv run python -m mas
```

5) Tail audit logs (optional)

```bash
uv run mas audit tail --last 10
# or: uv run python -m mas audit tail --last 10
```

## Example

End-to-end mTLS + request/reply:

```bash
redis-server
cd examples/e2e_ping_pong
bash make_certs.sh
uv run python -m mas
```

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format .
uv run pytest
uv run ty check
```
