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

### 2. Start MAS Service (Optional)
The service handles agent registration and discovery:
```bash
# In one terminal
uv run python -m mas.service
```

Or programmatically:
```python
import asyncio
from mas import MASService

async def main():
    service = MASService(redis_url="redis://localhost")
    await service.start()
    # Service runs in background
    # Keep running or await other operations
    await asyncio.sleep(3600)  # Run for 1 hour
    await service.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### 3. Create an Agent

```python
import asyncio
from pydantic import BaseModel
from mas import Agent, AgentMessage

class MyState(BaseModel):
    processed: int = 0

class ReplyRequest(BaseModel):
    hello: str

class MyAgent(Agent[MyState]):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(agent_id, state_model=MyState, **kwargs)

    @Agent.on("greeting.message", model=ReplyRequest)
    async def handle_greeting(self, message: AgentMessage, payload: ReplyRequest):
        # Use strongly-typed state
        self.state.processed += 1
        await self.update_state({"processed": self.state.processed})
        await message.reply("reply.message", {"reply": f"got {payload.hello}", "count": self.state.processed})

async def main():
    # Create and start agent
    agent = MyAgent("my_agent", capabilities=["chat", "nlp"])
    await agent.start()

    # Send typed message to another agent
    await agent.send("other_agent", "greeting.message", {"hello": "world"})

    # Discover agents by capability
    agents = await agent.discover(capabilities=["nlp"])
    print(f"Found {len(agents)} NLP agents")

    # Stop agent
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### 4. Use Decorator-Based Handlers (Recommended)

For better type safety and cleaner code, use decorator-based handlers:

```python
import asyncio
from pydantic import BaseModel, Field
from mas import Agent, AgentMessage

class GreetingRequest(BaseModel):
    name: str
    language: str = "en"

class TypedState(BaseModel):
    greetings_handled: int = Field(default=0)
    status_checks: int = Field(default=0)

class TypedAgent(Agent[TypedState]):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(agent_id, state_model=TypedState, **kwargs)

    @Agent.on("greeting.request", model=GreetingRequest)
    async def handle_greeting(self, message: AgentMessage, payload: GreetingRequest):
        """Handle greeting requests with typed payload"""
        self.state.greetings_handled += 1
        greeting = f"Hello, {payload.name}!" if payload.language == "en" else f"Hola, {payload.name}!"
        await self.update_state({"greetings_handled": self.state.greetings_handled})
        await message.reply("greeting.response", {"greeting": greeting, "handled": self.state.greetings_handled})
    
    @Agent.on("status.check")
    async def handle_status(self, message: AgentMessage, payload: None):
        """Handle status checks"""
        self.state.status_checks += 1
        await self.update_state({"status_checks": self.state.status_checks})
        await message.reply("status.response", {"status": "healthy", "checks": self.state.status_checks})

async def main():
    agent = TypedAgent("typed_agent")
    await agent.start()
    await asyncio.sleep(60)
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Messaging

Messages are routed through a centralized gateway for security and compliance:

Messages routed through centralized gateway for security and compliance:

```python
import asyncio
from mas import Agent

async def main():
    # Enable gateway mode
    agent = Agent("my_agent", use_gateway=True)
    await agent.start()
    
    # Messages now routed through gateway
    # Gateway provides: auth, authz, rate limiting, DLP, audit
    await agent.send("target_agent", "test.message", {"data": "hello"})
    
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

**Gateway capabilities:**
- **Authentication** - Token-based agent authentication
- **Authorization** - Role-based access control (RBAC)
- **Rate Limiting** - Per-agent token bucket rate limits
- **Data Loss Prevention** - PII/PHI detection and blocking
- **Audit Logging** - Complete immutable audit trail
- **Circuit Breakers** - Automatic failure isolation
- **Message Signing** - Cryptographic message verification
- **At-least-once delivery** - Uses Redis Streams for reliability

See [GATEWAY.md](GATEWAY.md) for complete gateway documentation.

Gateway mode is the default and recommended for production deployments.

## Features

### Auto-Persisted State

Agent state is automatically saved to Redis:

```python
from pydantic import BaseModel
from mas import Agent

class MyState(BaseModel):
    counter: int = 0
    status: str = "idle"

class MyAgent(Agent[MyState]):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(agent_id, state_model=MyState, **kwargs)

# Update state (automatically persisted)
agent = MyAgent("my_agent")
await agent.start()
await agent.update_state({"counter": 42, "status": "active"})
print(agent.state.counter)  # 42
await agent.stop()

# State survives restarts
agent2 = MyAgent("my_agent")  # Same ID
await agent2.start()
print(agent2.state.counter)  # Still 42
await agent2.stop()
```

### Discovery by Capabilities

Find agents by their capabilities:

```python
import asyncio
from mas import Agent

async def main():
    # Register with capabilities
    agent = Agent("my_agent", capabilities=["nlp", "text", "translation"])
    await agent.start()

    # Discover by capability
    nlp_agents = await agent.discover(capabilities=["nlp"])
    # Returns: [{"id": "my_agent", "capabilities": ["nlp", "text", "translation"], ...}]

    # Discover all active agents
    all_agents = await agent.discover()
    
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Lifecycle Hooks

Override hooks for custom initialization and cleanup:

```python
import asyncio
from mas import Agent, AgentMessage

class MyAgent(Agent):
    async def on_start(self):
        """Called when agent starts"""
        print("Agent starting...")
        await self.update_state({"status": "initializing"})
    
    async def on_stop(self):
        """Called when agent stops"""
        print("Agent stopping...")
        # await self.cleanup_resources()  # Your cleanup logic

async def main():
    agent = MyAgent("my_agent")
    await agent.start()
    await asyncio.sleep(5)
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Typed State with Pydantic

Use Pydantic models for type-safe state:

```python
import asyncio
from pydantic import BaseModel, Field
from mas import Agent

class MyState(BaseModel):
    counter: int = Field(default=0)
    name: str = Field(default="")
    active: bool = Field(default=True)

async def main():
    agent = Agent(
        "my_agent",
        state_model=MyState
    )
    await agent.start()

    # State is now typed
    await agent.update_state({"counter": 42})
    print(agent.state.counter)  # Properly typed as int
    
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Advanced Usage

### Custom Metadata

Provide metadata for discovery:

```python
import asyncio
from mas import Agent

class MyAgent(Agent):
    def get_metadata(self) -> dict:
        return {
            "version": "1.0.0",
            "model": "gpt-4",
            "region": "us-east-1"
        }

async def main():
    agent = MyAgent("my_agent")
    await agent.start()
    # Metadata is now available in agent registry
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Message Handling Patterns

Use decorator-based handlers for type-safe message handling:

```python
import asyncio
from pydantic import BaseModel, Field
from mas import Agent, AgentMessage

class ChatRequest(BaseModel):
    text: str
    user_id: str

class SummarizeRequest(BaseModel):
    text: str

class ChatState(BaseModel):
    messages: int = Field(default=0)
    summaries: int = Field(default=0)

class ChatAgent(Agent[ChatState]):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(agent_id, state_model=ChatState, **kwargs)

    @Agent.on("chat.message", model=ChatRequest)
    async def handle_chat(self, message: AgentMessage, payload: ChatRequest):
        """Handle chat messages with typed payload"""
        self.state.messages += 1
        response = f"You said: {payload.text}"
        await self.update_state({"messages": self.state.messages})
        await message.reply("chat.response", {"response": response, "count": self.state.messages})
    
    @Agent.on("summarize.request", model=SummarizeRequest)
    async def handle_summarize(self, message: AgentMessage, payload: SummarizeRequest):
        """Handle summarize requests"""
        self.state.summaries += 1
        await self.update_state({"summaries": self.state.summaries})
        summary = self.summarize(payload.text)
        await message.reply("summarize.response", {"summary": summary, "count": self.state.summaries})

async def main():
    agent = ChatAgent("chat_agent")
    await agent.start()
    # Agent now handles messages based on message type
    await asyncio.sleep(60)  # Run for 1 minute
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Running Multiple Agents

```python
import asyncio
from mas import MASService, Agent

async def main():
    # Start MAS service
    service = MASService()
    await service.start()

    # Create multiple agents
    agents = [
        Agent("agent_1", capabilities=["nlp"]),
        Agent("agent_2", capabilities=["vision"]),
        Agent("agent_3", capabilities=["math"]),
    ]

    # Start all
    for agent in agents:
        await agent.start()

    # Agents can now discover and message each other
    nlp_agents = await agents[1].discover(capabilities=["nlp"])
    await agents[1].send("agent_1", "task.message", {"task": "analyze text"})

    # Let agents run
    await asyncio.sleep(10)

    # Stop all
    for agent in agents:
        await agent.stop()

    await service.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Examples

### Chemistry Tutoring

Two OpenAI-powered agents exchanging information:

- **Student Agent**: Asks chemistry homework questions
- **Professor Agent**: Provides educational explanations
- **Use case**: Educational demo showcasing agent discovery and messaging

```bash
cd examples/chemistry_tutoring
echo "OPENAI_API_KEY=your-key-here" >> ../../.env
./run.sh
```

See [examples/chemistry_tutoring/README.md](examples/chemistry_tutoring/README.md) for details.

### Healthcare Consultation (Gateway Mode)

Enterprise-grade messaging with security and compliance:

- **Patient Agent**: Asks healthcare questions
- **Doctor Agent**: Provides medical advice
- **Mode**: Gateway (authentication, authorization, DLP, audit)
- **Use case**: Production, HIPAA/SOC2/GDPR compliance

```bash
cd examples/healthcare_consultation
echo "OPENAI_API_KEY=your-key-here" >> ../../.env
./run.sh
```

**Gateway features demonstrated:**
- ✅ Authentication & authorization (RBAC)
- ✅ Rate limiting (token bucket)
- ✅ Data Loss Prevention (PHI/PII detection)
- ✅ Complete audit trail (Redis Streams)
- ✅ Circuit breakers
- ✅ At-least-once delivery

See [examples/healthcare_consultation/README.md](examples/healthcare_consultation/README.md) for details.

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

Performance benchmarks are planned for future releases.

## Documentation

- **[Architecture Guide](ARCHITECTURE.md)** - Architecture, design decisions, and implementation details
- **[Gateway Guide](GATEWAY.md)** - Enterprise gateway pattern with security, audit, and compliance features
- **[API Reference](#messaging-modes)** - Feature documentation and usage examples

### Quick Architecture Overview

**Core Components:**
- **MAS Service** - Agent registry and health monitor (optional)
- **Agent** - Base class for implementing agents
- **Gateway Service** - Optional security/audit layer for enterprise deployments
- **Registry** - Agent discovery by capabilities
- **State Manager** - State persistence to Redis

**Message Flow:**

Gateway Mode:
```
Agent A → Gateway Service → Redis Streams → Agent B
          (validation)       (reliable delivery)
```

**Redis Keys:**
- `agent:{id}` - Agent metadata
- `agent:{id}:heartbeat` - Health monitoring (60s TTL)
- `agent.state:{id}` - Persisted agent state
- `agent.stream:{id}` - Message stream (gateway mode)
- `mas.system` - System events (pub/sub)

For detailed architecture information, see [ARCHITECTURE.md](ARCHITECTURE.md).

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
- [ ] Priority queue for gateway mode
- [ ] Enhanced metrics and observability
- [ ] Performance benchmarks for both modes
- [ ] Prometheus metrics integration
- [ ] Management dashboard

### Under Consideration
- [ ] Multi-region support
- [ ] Message replay functionality
- [ ] Dead letter queues
- [ ] Management dashboard

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
uv run basedpyright

# Format code
uv run ruff format .

# Lint code
uv run ruff check .
```

## License

MIT License - see LICENSE file for details
