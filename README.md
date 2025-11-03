# MAS Framework - Multi-Agent System

A Python framework for building multi-agent systems with Redis.

## Architecture

```
MAS Service (Registry & Discovery)
         ↓ ↑
       Redis
         ↓ ↑
  Agent ↔ Agent (Peer-to-Peer)
```

**Key Design Principles:**
- **Peer-to-peer messaging** - Agents communicate directly via Redis pub/sub channels
- **MAS Service** - Provides agent registration and discovery (does not route messages)
- **Auto-persisted state** - Agent state saved to Redis hash structures
- **Direct communication** - No central message router or bottleneck

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
from mas import MASService

service = MASService(redis_url="redis://localhost")
await service.start()
```

### 3. Create an Agent

```python
from mas import Agent, AgentMessage

class MyAgent(Agent):
    async def on_message(self, message: AgentMessage):
        print(f"Received: {message.payload}")
        # Send reply
        await self.send(message.sender_id, {"reply": "got it"})

# Create and start agent
agent = MyAgent("my_agent", capabilities=["chat", "nlp"])
await agent.start()

# Send message to another agent
await agent.send("other_agent", {"hello": "world"})

# Discover agents by capability
agents = await agent.discover(capabilities=["nlp"])
print(f"Found {len(agents)} NLP agents")

# Stop agent
await agent.stop()
```

## Features

### Peer-to-Peer Messaging

Agents communicate directly without going through a central router:

```python
# Direct send (publishes to Redis channel: agent.target_id)
await agent.send("target_agent", {"data": "hello"})
```

**Characteristics:**
- Direct agent-to-agent communication via Redis pub/sub
- No central message routing overhead
- Message delivery depends on Redis pub/sub semantics
- Agents can run on different machines connected to same Redis instance

### Auto-Persisted State

Agent state is automatically saved to Redis:

```python
# Update state (automatically persisted)
await agent.update_state({"counter": 42, "status": "active"})

# Access state
print(agent.state["counter"])  # "42"

# State survives restarts
await agent.stop()
agent2 = MyAgent("my_agent")  # Same ID
await agent2.start()
print(agent2.state["counter"])  # Still "42"
```

### Discovery by Capabilities

Find agents by their capabilities:

```python
# Register with capabilities
agent = Agent("my_agent", capabilities=["nlp", "text", "translation"])
await agent.start()

# Discover by capability
nlp_agents = await agent.discover(capabilities=["nlp"])
# Returns: [{"id": "my_agent", "capabilities": ["nlp", "text", "translation"], ...}]

# Discover all active agents
all_agents = await agent.discover()
```

### Lifecycle Hooks

Override hooks for custom initialization and cleanup:

```python
class MyAgent(Agent):
    async def on_start(self):
        """Called when agent starts"""
        print("Agent starting...")
        await self.update_state({"status": "initializing"})
    
    async def on_stop(self):
        """Called when agent stops"""
        print("Agent stopping...")
        await self.cleanup_resources()
    
    async def on_message(self, message: AgentMessage):
        """Called when message received"""
        print(f"Got message: {message.payload}")
```

### Typed State with Pydantic

Use Pydantic models for type-safe state:

```python
from pydantic import BaseModel, Field

class MyState(BaseModel):
    counter: int = Field(default=0)
    name: str = Field(default="")
    active: bool = Field(default=True)

agent = Agent(
    "my_agent",
    state_model=MyState
)
await agent.start()

# State is now typed
await agent.update_state({"counter": 42})
print(agent.state.counter)  # Properly typed as int
```

## Advanced Usage

### Custom Metadata

Provide metadata for discovery:

```python
class MyAgent(Agent):
    def get_metadata(self) -> dict:
        return {
            "version": "1.0.0",
            "model": "gpt-4",
            "region": "us-east-1"
        }
```

### Message Handling Patterns

```python
class ChatAgent(Agent):
    async def on_message(self, message: AgentMessage):
        # Pattern matching on payload
        match message.payload.get("action"):
            case "chat":
                await self.handle_chat(message)
            case "summarize":
                await self.handle_summarize(message)
            case _:
                await self.send(message.sender_id, {"error": "unknown action"})
    
    async def handle_chat(self, message: AgentMessage):
        response = f"You said: {message.payload['text']}"
        await self.send(message.sender_id, {"response": response})
```

### Running Multiple Agents

```python
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
await agents[1].send("agent_1", {"task": "analyze text"})

# Stop all
for agent in agents:
    await agent.stop()

await service.stop()
```

## Examples

### Chemistry Tutoring Demo

A complete example showing two OpenAI-powered agents exchanging information:

- **Student Agent**: Asks chemistry homework questions
- **Professor Agent**: Provides educational explanations

```bash
cd examples/chemistry_tutoring

# Add your OpenAI API key to .env file in project root
echo "OPENAI_API_KEY=your-key-here" >> ../../.env

# Run the demo (installs dependencies automatically)
./run.sh

# Or manually with uv
uv pip install openai python-dotenv
uv run python main.py
```

See [examples/chemistry_tutoring/README.md](examples/chemistry_tutoring/README.md) for full documentation.

## Testing

```bash
# Run tests (requires Redis running on localhost:6379)
uv run pytest

# Run specific test
uv run pytest tests/test_simple_messaging.py::test_peer_to_peer_messaging

# Run with coverage
uv run pytest --cov=src/mas
```

## Performance Characteristics

The framework uses Redis pub/sub for messaging. Performance depends on:
- Redis instance configuration and network latency
- Message payload size
- Number of concurrent agents
- Agent processing logic

Performance benchmarks are planned for future releases.

## Documentation

- **[Architecture Guide](ARCHITECTURE.md)** - Detailed system architecture, design decisions, and implementation details
- **[API Reference](#features)** - Feature documentation and usage examples below

### Quick Architecture Overview

**Components:**
- **MAS Service** - Agent registry and health monitor (optional)
- **Agent** - Base class for implementing agents with peer-to-peer messaging
- **Registry** - Agent discovery by capabilities
- **State Manager** - State persistence to Redis

**Message Flow (Peer-to-Peer):**
```
Agent A → Redis Pub/Sub (channel: agent.B) → Agent B
```

**Redis Keys:**
- `agent:{id}` - Agent metadata
- `agent:{id}:heartbeat` - Health monitoring (60s TTL)
- `agent.state:{id}` - Persisted agent state
- `agent.{id}` - Message channel (pub/sub)
- `mas.system` - System events (pub/sub)

For detailed architecture information, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Roadmap

### Current Features
- Peer-to-peer messaging
- Redis-based registry
- Auto-persisted state
- Simple agent API
- Discovery by capabilities
- Heartbeat monitoring

### In Development
- [ ] Authentication and authorization (see `src/mas/gateway/`)
- [ ] Message delivery guarantees with Redis Streams
- [ ] Circuit breakers for failing agents
- [ ] Rate limiting
- [ ] Performance benchmarks
- [ ] Prometheus metrics integration

### Under Consideration
- [ ] Multi-region support
- [ ] Message replay functionality
- [ ] Dead letter queues
- [ ] Management dashboard

## FAQ

**Q: Why Redis?**
A: Redis provides pub/sub for messaging, hash structures for state, and TTL for heartbeats. It's a single dependency with well-understood operational characteristics.

**Q: What if Redis goes down?**
A: Agents will lose connection and cannot communicate. Consider Redis Cluster or Sentinel for high availability in production.

**Q: Can agents run on different machines?**
A: Yes. All agents connect to the same Redis instance via the redis:// URL.

**Q: How many agents can I run?**
A: The framework has been tested with small numbers of agents. Limits depend on Redis capacity and agent workload.

**Q: Message delivery guarantees?**
A: Currently uses Redis pub/sub (at-most-once delivery). Support for Redis Streams (at-least-once) is under development.

## Development

This project uses `uv` as the package manager.

```bash
# Install dependencies
uv sync

# Run tests (requires Redis on localhost:6379)
uv run pytest

# Run type checker
uv run pyright

# Format code
uv run ruff format .

# Lint code
uv run ruff check .
```

## License

MIT License - see LICENSE file for details
