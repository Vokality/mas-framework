# MAS Framework - Multi-Agent System

A Python framework for building multi-agent systems with Redis. Messages are routed through a centralized gateway that provides security, reliability, and observability.

## Architecture

### Gateway Architecture
```
Agent A → Gateway Service → Redis Streams → Agent B
          (auth, authz,
           rate limit,
           audit, DLP)
```

**Key Capabilities:**
- **Gateway-mediated messaging** - Centralized validation and routing
- **Agent registry** - Capability-based discovery via MAS Service
- **Auto-persisted state** - Agent state saved to Redis hash structures
- **Decorator-based handlers** - Type-safe message handling with Pydantic models
- **Strongly-typed state** - Optional Pydantic models for type-safe state management

## Quick Start

### 1. Start Redis

```bash
redis-server
```

### 2. Define your agents (`agents.yaml`)

Create an `agents.yaml` in your project root (the runner searches upward from the current working directory).

```yaml
# agents.yaml

# Optional: start MAS service (health monitoring + logs)
start_service: true
service_redis_url: redis://localhost:6379

# Gateway is required (agents always route via the gateway)
start_gateway: true

# Default is deny-by-default; define permissions so agents can talk
permissions:
  - type: allow_bidirectional
    agents: [echo, greeter]

agents:
  - agent_id: echo
    class_path: my_agents:EchoAgent
    init_kwargs:
      capabilities: [echo]

  - agent_id: greeter
    class_path: my_agents:GreeterAgent
    init_kwargs:
      capabilities: [demo]
```

Notes:
- `class_path` uses `module:ClassName` format.
- `init_kwargs` are passed to your agent constructor (except `agent_id`, which is reserved).
- Use `instances: N` on an agent spec to run multiple instances.

### 3. Implement your agents (`my_agents.py`)

Create `my_agents.py` next to `agents.yaml`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field

from mas import Agent, AgentMessage


class GreetingRequest(BaseModel):
    hello: str


class EchoState(BaseModel):
    seen: int = Field(default=0)


class EchoAgent(Agent[EchoState]):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(agent_id, state_model=EchoState, **kwargs)

    @Agent.on("greeting.request", model=GreetingRequest)
    async def handle_greeting(self, message: AgentMessage, payload: GreetingRequest) -> None:
        self.state.seen += 1
        await self.update_state({"seen": self.state.seen})
        await message.reply(
            "greeting.response",
            {"reply": f"echo: {payload.hello}", "seen": self.state.seen},
        )


class GreeterAgent(Agent[dict[str, object]]):
    async def on_start(self) -> None:
        response = await self.request(
            "echo",
            "greeting.request",
            {"hello": "world"},
            timeout=5,
        )
        print("Got response:", response.data)
```

### 4. Run MAS

```bash
uv run python -m mas
```

Stop with Ctrl+C.

Optional: create `gateway.yaml` next to `agents.yaml` (or copy `gateway.yaml.example`) to configure gateway settings.

## Messaging

Messages are routed through a centralized gateway for security and compliance.
This assumes a gateway process is running (the runner `uv run python -m mas` starts it for you).

- Use `await agent.send(target_id, message_type, data)` for fire-and-forget.
- Use `await agent.request(...)` + `await message.reply(...)` for request/response.
- Gateway authz is deny-by-default; configure `permissions:` in `agents.yaml`.

**Gateway capabilities:**
- **Authentication** - Instance-scoped bearer tokens (stored as hashes in Redis)
- **Authorization** - Role-based access control (RBAC)
- **Rate Limiting** - Per-agent token bucket rate limits
- **Data Loss Prevention** - PII/PHI detection and blocking
- **Audit Logging** - Complete immutable audit trail
- **Circuit Breakers** - Automatic failure isolation
- **Message Signing** - Cryptographic message verification
- **At-least-once delivery** - Uses Redis Streams for reliability

See [GATEWAY.md](GATEWAY.md) for complete gateway documentation.

## Features

- Typed handlers: `@Agent.on("message.type", model=YourModel)`
- Auto-persisted state: `agent.state:{id}`
- Discovery by capability: `await agent.discover(...)`
- Multi-instance scaling: set `instances: N` in `agents.yaml`
- Request/response: `await agent.request(...)` + `await message.reply(...)`

See `DOCUMENTATION.md` for more details.

## Examples

- `examples/chemistry_tutoring/`
- `examples/healthcare_consultation/`

Each example directory has its own README.

## Testing

```bash
# Run tests (requires Redis running on localhost:6379)
uv run pytest

# Run specific test
uv run pytest tests/test_simple_messaging.py::test_bidirectional_messaging

# Run with coverage
uv run pytest --cov=src/mas
```

## Performance Characteristics

The framework uses Redis Streams for reliable messaging through the gateway. Performance depends on:
- Redis instance configuration and network latency
- Message payload size
- Number of concurrent agents
- Agent processing logic

See `tests/test_performance.py` for benchmarks (requires Redis on localhost:6379).

## Documentation

- **[Architecture Guide](ARCHITECTURE.md)** - Architecture, design decisions, and implementation details
- **[Gateway Guide](GATEWAY.md)** - Enterprise gateway pattern with security, audit, and compliance features
- **[Documentation](DOCUMENTATION.md)** - Usage notes and API details

## Roadmap

### Current Features
- ✅ Gateway-mediated messaging with security controls
- ✅ Authentication and authorization (RBAC)
- ✅ Rate limiting (token bucket)
- ✅ Data loss prevention (DLP)
- ✅ Audit logging (Redis Streams)
- ✅ Circuit breakers
- ✅ Message signing and verification
- ✅ Auto-persisted state
- ✅ Discovery by capabilities
- ✅ Heartbeat monitoring

### In Development
- [ ] Enhanced metrics and observability
- [ ] Management dashboard

### Under Consideration
- [ ] Multi-region support
- [ ] Message replay functionality

## FAQ

**Q: Why Redis?**
A: Redis provides Streams for reliable delivery via the gateway, hash structures for state, and TTL for heartbeats. Single dependency with well-understood operational characteristics.

**Q: What if Redis goes down?**
A: Agents will lose connection and cannot communicate. Consider Redis Cluster or Sentinel for high availability in production.

**Q: Can agents run on different machines?**
A: Yes. All agents connect to the same Redis instance via the redis:// URL.

**Q: How many agents can I run?**
A: The framework has been tested with small numbers of agents. Limits depend on Redis capacity and agent workload.

**Q: Message delivery guarantees?**
A: At-least-once via Redis Streams through the gateway.

## Development

This project uses `uv` as the package manager.

```bash
# Install dependencies
uv sync

# Run tests (requires Redis on localhost:6379)
uv run pytest

# Run type checker
uvx ty check

# Format code
uv run ruff format .

# Lint code
uv run ruff check .
```

## License

MIT License - see LICENSE file for details
