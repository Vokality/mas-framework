# MAS Framework Documentation

**Multi-Agent System Framework for Python**

This guide is for developers building applications with the MAS Framework. If you're looking to contribute to the framework itself, see [ARCHITECTURE.md](ARCHITECTURE.md) instead.

## Table of Contents

- [Introduction](#introduction)
- [Core Concepts](#core-concepts)
- [Getting Started](#getting-started)
- [Building Your First Agent](#building-your-first-agent)
- [Messaging Patterns](#messaging-patterns)
- [Agent Discovery](#agent-discovery)
- [State Management](#state-management)
- [Messaging Modes](#messaging-modes)
- [Gateway Mode (Enterprise)](#gateway-mode-enterprise)
- [Advanced Patterns](#advanced-patterns)
- [Best Practices](#best-practices)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)
- [API Reference](#api-reference)

---

## Introduction

MAS Framework is a lightweight Python framework for building multi-agent systems backed by Redis. It enables multiple autonomous agents to communicate, collaborate, and coordinate their actions in distributed systems.

### What is a Multi-Agent System?

A multi-agent system consists of multiple autonomous software agents that:
- Have their own state and behavior
- Communicate by sending messages
- Discover and interact with other agents
- Work together to accomplish complex tasks

### Key Features

- **Peer-to-Peer Messaging**: Agents communicate directly via Redis pub/sub (default mode)
- **Gateway Mode**: Enterprise-grade routing with security and compliance (optional)
- **Agent Discovery**: Find agents by their capabilities
- **Auto-Persisted State**: Agent state automatically saved to Redis
- **Request-Response**: Built-in support for synchronous request-response patterns
- **Async/Await**: Fully asynchronous using Python's asyncio
- **Type Safety**: Pydantic models for typed state management

### Use Cases

- **Distributed AI Systems**: Multiple AI agents collaborating on complex tasks
- **Microservices Coordination**: Services discovering and messaging each other
- **Workflow Orchestration**: Agents coordinating multi-step processes
- **Healthcare Systems**: HIPAA-compliant agent communication (gateway mode)
- **Financial Services**: SOC2/PCI-compliant agent interactions (gateway mode)
- **Educational Platforms**: Tutoring systems with multiple specialized agents

---

## Core Concepts

### Agents

An **agent** is an autonomous entity that:
- Has a unique identifier (`agent_id`)
- Registers with capabilities (e.g., `["nlp", "translation"]`)
- Sends and receives messages
- Maintains persistent state
- Can discover other agents

### Messages

Messages are structured data objects sent between agents:
```python
class AgentMessage:
    sender_id: str      # Who sent the message
    target_id: str      # Who should receive it
    payload: dict       # The actual message content
    timestamp: float    # When it was sent
    message_id: str     # Unique identifier
```

### Capabilities

Capabilities are tags that describe what an agent can do. Examples:
- `"nlp"` - Natural language processing
- `"translation"` - Language translation
- `"image_analysis"` - Image processing
- `"database_query"` - Database operations

Agents register with capabilities, and other agents discover them by searching for specific capabilities.

### State

Each agent has persistent state stored in Redis. State survives agent restarts and can be:
- Simple dictionary (flexible)
- Pydantic model (typed and validated)

### Registry

The **registry** is a Redis-backed service that:
- Tracks which agents are active
- Stores agent capabilities and metadata
- Enables discovery queries
- Monitors agent health via heartbeats

---

## Getting Started

### Prerequisites

1. **Python 3.11+**
2. **Redis 5.0+** (running locally or remotely)
3. **uv** package manager (recommended) or pip

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/mas-framework.git
cd mas-framework

# Install with uv (recommended)
uv sync

# Or install with pip
pip install -e .
```

### Start Redis

```bash
# macOS with Homebrew
brew install redis
brew services start redis

# Or with Docker
docker run -d -p 6379:6379 redis:latest

# Verify Redis is running
redis-cli ping  # Should return "PONG"
```

### Verify Installation

```bash
# Run tests
uv run pytest

# Run example
cd examples/chemistry_tutoring
./run.sh
```

---

## Building Your First Agent

### Minimal Example

Here's the simplest possible agent:

```python
import asyncio
from mas import Agent, AgentMessage

class HelloAgent(Agent):
    async def on_message(self, message: AgentMessage):
        """Handle incoming messages"""
        print(f"Received: {message.payload}")

async def main():
    # Create and start agent
    agent = HelloAgent("hello_agent", capabilities=["greeting"])
    await agent.start()
    
    # Send a message to another agent
    await agent.send("other_agent", {"text": "Hello, world!"})
    
    # Keep running for 60 seconds
    await asyncio.sleep(60)
    
    # Stop the agent
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Two Agents Communicating

Let's create two agents that talk to each other:

```python
import asyncio
from mas import Agent, AgentMessage

class SenderAgent(Agent):
    async def on_start(self):
        """Called when agent starts"""
        print(f"{self.id} is ready!")
        
        # Discover the receiver
        agents = await self.discover(capabilities=["receiver"])
        if agents:
            receiver_id = agents[0]["id"]
            print(f"Found receiver: {receiver_id}")
            
            # Send a message
            await self.send(receiver_id, {
                "greeting": "Hello from sender!",
                "timestamp": time.time()
            })

class ReceiverAgent(Agent):
    async def on_message(self, message: AgentMessage):
        """Handle incoming messages"""
        print(f"Received from {message.sender_id}: {message.payload}")
        
        # Send a reply
        await self.send(message.sender_id, {
            "reply": "Hello back!",
            "received_at": time.time()
        })

async def main():
    # Start receiver first
    receiver = ReceiverAgent("receiver_agent", capabilities=["receiver"])
    await receiver.start()
    
    # Start sender
    sender = SenderAgent("sender_agent", capabilities=["sender"])
    await sender.start()
    
    # Let them communicate
    await asyncio.sleep(5)
    
    # Stop both
    await sender.stop()
    await receiver.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Agent Lifecycle Hooks

Override these methods to customize agent behavior:

```python
class MyAgent(Agent):
    async def on_start(self):
        """Called when agent starts - use for initialization"""
        print("Agent starting...")
        await self.update_state({"initialized": True})
    
    async def on_stop(self):
        """Called when agent stops - use for cleanup"""
        print("Agent stopping...")
        # Close connections, save final state, etc.
    
    async def on_message(self, message: AgentMessage):
        """Called when message received - main message handler"""
        print(f"Got message: {message.payload}")
```

---

## Messaging Patterns

### Fire-and-Forget (Send)

Send a message without waiting for a response:

```python
# Sender
await agent.send("target_agent", {
    "action": "process",
    "data": [1, 2, 3]
})

# Receiver
async def on_message(self, message: AgentMessage):
    if message.payload.get("action") == "process":
        data = message.payload["data"]
        result = self.process(data)
        # Do something with result
```

### Request-Response

Send a message and wait for a response:

```python
# Requester
response = await agent.request(
    "calculator_agent",
    {"operation": "add", "numbers": [1, 2, 3]},
    timeout=10.0  # Wait up to 10 seconds
)
result = response.payload["result"]
print(f"Result: {result}")

# Responder
async def on_message(self, message: AgentMessage):
    if message.expects_reply:
        # Process the request
        numbers = message.payload["numbers"]
        result = sum(numbers)
        
        # Send reply using message.reply()
        await message.reply({"result": result})
```

**Key Points:**
- `request()` automatically handles correlation IDs
- Responder uses `message.reply()` to send response
- `message.expects_reply` tells responder a reply is expected
- Timeout is configurable (default: 30 seconds)

### Broadcast Pattern

Send a message to all agents with a specific capability:

```python
async def broadcast_to_workers(self):
    # Discover all workers
    workers = await self.discover(capabilities=["worker"])
    
    # Send task to each worker
    for worker in workers:
        await self.send(worker["id"], {
            "task": "process_chunk",
            "chunk_id": worker["id"]
        })
```

### Pub-Sub Pattern

Multiple agents listen for specific message types:

```python
class SubscriberAgent(Agent):
    async def on_message(self, message: AgentMessage):
        msg_type = message.payload.get("type")
        
        if msg_type == "notification":
            await self.handle_notification(message.payload)
        elif msg_type == "alert":
            await self.handle_alert(message.payload)

# Publisher
class PublisherAgent(Agent):
    async def publish_notification(self, text: str):
        # Get all subscribers
        subscribers = await self.discover(capabilities=["subscriber"])
        
        # Send to each
        for sub in subscribers:
            await self.send(sub["id"], {
                "type": "notification",
                "text": text
            })
```

### Pipeline Pattern

Chain agents together for multi-step processing:

```python
class StageOneAgent(Agent):
    """First stage of pipeline"""
    async def on_message(self, message: AgentMessage):
        # Process data
        input_data = message.payload["data"]
        processed = self.stage_one_processing(input_data)
        
        # Send to next stage
        stage_two = await self.discover(capabilities=["stage_two"])
        if stage_two:
            await self.send(stage_two[0]["id"], {
                "data": processed,
                "stage": 2
            })

class StageTwoAgent(Agent):
    """Second stage of pipeline"""
    async def on_message(self, message: AgentMessage):
        if message.payload.get("stage") == 2:
            data = message.payload["data"]
            final_result = self.stage_two_processing(data)
            
            # Send to original requester or final destination
            await self.send(message.sender_id, {
                "result": final_result,
                "completed": True
            })
```

---

## Agent Discovery

### Basic Discovery

Find all active agents:

```python
all_agents = await agent.discover()
print(f"Found {len(all_agents)} active agents")

for agent_info in all_agents:
    print(f"- {agent_info['id']}: {agent_info['capabilities']}")
```

### Discovery by Capability

Find agents with specific capabilities:

```python
# Find agents that can translate
translators = await agent.discover(capabilities=["translation"])

# Find agents that can do NLP
nlp_agents = await agent.discover(capabilities=["nlp"])

# Find agents with multiple capabilities (OR logic)
specialists = await agent.discover(capabilities=["nlp", "translation"])
# Returns agents with NLP OR translation (or both)
```

### Discovery Response Format

Discovery returns a list of dictionaries:

```python
agents = await agent.discover(capabilities=["nlp"])
# [
#   {
#     "id": "nlp_agent_1",
#     "capabilities": ["nlp", "sentiment"],
#     "metadata": {"version": "1.0", "model": "gpt-4"}
#   },
#   {
#     "id": "nlp_agent_2", 
#     "capabilities": ["nlp", "translation"],
#     "metadata": {"version": "2.0", "languages": ["en", "es"]}
#   }
# ]
```

### Using Discovery Results

```python
# Find a specific type of agent
nlp_agents = await self.discover(capabilities=["nlp"])
if not nlp_agents:
    print("No NLP agents available")
    return

# Pick the first available agent
chosen_agent = nlp_agents[0]
agent_id = chosen_agent["id"]

# Send message
await self.send(agent_id, {
    "text": "Analyze this text",
    "operation": "sentiment"
})
```

### Custom Metadata

Provide metadata for discovery:

```python
class MyAgent(Agent):
    def get_metadata(self) -> dict:
        """Override to provide custom metadata"""
        return {
            "version": "2.1.0",
            "model": "gpt-4",
            "languages": ["en", "es", "fr"],
            "max_tokens": 4096
        }

# Agents discovering this agent will see the metadata
agents = await other_agent.discover(capabilities=["translation"])
# agents[0]["metadata"] contains the dictionary above
```

### Load Balancing with Discovery

Distribute work across multiple agents:

```python
async def distribute_work(self, tasks: list):
    # Find all available workers
    workers = await self.discover(capabilities=["worker"])
    
    if not workers:
        print("No workers available")
        return
    
    # Round-robin distribution
    for i, task in enumerate(tasks):
        worker = workers[i % len(workers)]
        await self.send(worker["id"], {
            "task": task,
            "task_id": i
        })
```

---

## State Management

### Basic State Operations

Agents have automatic state persistence:

```python
class StatefulAgent(Agent):
    async def on_start(self):
        # State is automatically loaded from Redis
        print(f"Current state: {self.state}")
        
        # Update state (automatically persisted to Redis)
        await self.update_state({
            "counter": 0,
            "status": "active"
        })
    
    async def on_message(self, message: AgentMessage):
        # Access state
        current_count = int(self.state.get("counter", 0))
        
        # Update state
        await self.update_state({"counter": current_count + 1})
        
        print(f"Processed {current_count + 1} messages")
```

### State Persistence

State automatically persists across restarts:

```python
# First run
agent = MyAgent("my_agent")
await agent.start()
await agent.update_state({"counter": 42, "name": "Alice"})
await agent.stop()

# Second run (after restart)
agent = MyAgent("my_agent")  # Same ID!
await agent.start()
print(agent.state["counter"])  # Prints: 42
print(agent.state["name"])     # Prints: Alice
```

### Typed State with Pydantic

Use Pydantic for type-safe state:

```python
from pydantic import BaseModel, Field

class MyAgentState(BaseModel):
    counter: int = Field(default=0)
    status: str = Field(default="idle")
    items: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

class TypedAgent(Agent):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(
            agent_id,
            state_model=MyAgentState,  # Use typed state
            **kwargs
        )
    
    async def on_message(self, message: AgentMessage):
        # State is now typed!
        self.state.counter += 1  # Type: int
        self.state.items.append(message.payload["item"])  # Type: list[str]
        
        # Updates are validated by Pydantic
        await self.update_state({
            "counter": self.state.counter,
            "items": self.state.items
        })
```

**Benefits of Typed State:**
- IDE autocomplete
- Type checking
- Validation (catches errors early)
- Default values
- Complex nested structures

### State Reset

Clear agent state:

```python
# Reset to defaults
await agent.reset_state()

# State is now:
# - {} for dict-based state
# - Default Pydantic model for typed state
```

---

## Messaging Modes

MAS Framework supports two messaging modes:

### Comparison Table

| Feature | Peer-to-Peer (Default) | Gateway Mode |
|---------|------------------------|--------------|
| **Latency** | <5ms | 10-20ms |
| **Throughput** | 10,000+ msg/s | 2,000-5,000 msg/s |
| **Delivery** | At-most-once | At-least-once |
| **Authentication** | None | Token-based |
| **Authorization** | None | RBAC |
| **Rate Limiting** | None | Yes |
| **DLP** | None | Yes |
| **Audit Trail** | None | Complete |
| **Use Case** | Dev/test, high-speed | Production, compliance |

---

## Peer-to-Peer Mode (Default)

### Overview

In peer-to-peer mode:
- Agents communicate directly via Redis pub/sub
- No central message routing
- Lowest latency and highest throughput
- Suitable for development and high-performance scenarios

### Usage

P2P is the default mode, no configuration needed:

```python
agent = Agent("my_agent")
await agent.start()

# Messages go directly via Redis pub/sub
await agent.send("target_agent", {"data": "hello"})
```

### Characteristics

**Pros:**
- ✅ Lowest latency (<5ms)
- ✅ Highest throughput (10,000+ msg/s)
- ✅ Simple setup
- ✅ No additional infrastructure

**Cons:**
- ❌ No audit trail
- ❌ No message persistence
- ❌ No built-in security
- ❌ At-most-once delivery only

### When to Use P2P

Use peer-to-peer mode when:
- Building prototypes or MVP
- Running in trusted environments
- Latency is critical (e.g., real-time systems)
- No compliance requirements
- Simple agent-to-agent communication

---

## Gateway Mode (Enterprise)

### Overview

Gateway mode routes all messages through a centralized gateway that provides:
- **Authentication**: Token-based agent validation
- **Authorization**: Role-based access control (RBAC)
- **Rate Limiting**: Prevent abuse and overload
- **DLP**: Data Loss Prevention (PII/PHI detection)
- **Audit Logging**: Complete immutable audit trail
- **Circuit Breakers**: Failure isolation
- **Message Signing**: Cryptographic verification
- **Reliable Delivery**: At-least-once guarantees via Redis Streams

### Basic Setup

```python
from mas import Agent, GatewayService

async def main():
    # 1. Start gateway service
    gateway = GatewayService()  # Uses default redis://localhost:6379
    await gateway.start()
    
    # 2. Create agent with gateway mode enabled
    agent = Agent(
        "my_agent",
        capabilities=["worker"],
        use_gateway=True  # Enable gateway mode
    )
    
    # 3. Connect agent to gateway
    agent.set_gateway(gateway)
    
    # 4. Start agent (will authenticate with gateway)
    await agent.start()
    
    # 5. Messages now route through gateway
    await agent.send("target_agent", {"data": "secure message"})
    
    # Cleanup
    await agent.stop()
    await gateway.stop()
```

### Gateway Configuration

Configure security features:

```python
from mas.gateway import GatewayService, GatewaySettings

# Option 1: Programmatic configuration
settings = GatewaySettings(
    redis={"url": "redis://localhost:6379"},
    rate_limit={"per_minute": 100, "per_hour": 1000},
    features={
        "dlp": True,              # Enable DLP scanning
        "rbac": True,             # Enable RBAC authorization
        "message_signing": True,  # Enable message signing
        "circuit_breaker": True   # Enable circuit breakers
    }
)

gateway = GatewayService(settings=settings)
await gateway.start()

# Option 2: Load from YAML file
settings = GatewaySettings.from_yaml("gateway.yaml")
gateway = GatewayService(settings=settings)
await gateway.start()
```

Example `gateway.yaml`:

```yaml
redis:
  url: redis://localhost:6379
  
rate_limit:
  per_minute: 100
  per_hour: 1000
  
features:
  dlp: true
  rbac: true
  message_signing: true
  circuit_breaker: true
```

### Security Features

#### Authentication

Agents authenticate using tokens:

```python
# Tokens are automatically generated on registration
agent = Agent("my_agent", use_gateway=True)
await agent.start()  # Token stored in agent._token

# Agent token is automatically included in messages
await agent.send("target", {"data": "hello"})
# Gateway validates token before routing
```

#### Authorization (RBAC)

Configure role-based permissions:

```python
# Get authorization manager
auth = gateway.auth_manager()

# Allow bidirectional communication (most common)
await auth.allow_bidirectional("agent_a", "agent_b")

# Allow one-way communication
await auth.allow_broadcast("coordinator", ["worker1", "worker2"])

# Allow full mesh network
await auth.allow_network(["agent1", "agent2", "agent3"])

# Wildcard permission (admin can message anyone)
await auth.allow_wildcard("admin_agent")

# Chainable configuration
await (auth
    .allow("agent1", "agent2")
    .allow("agent2", ["agent3", "agent4"])
    .apply())
```

#### Rate Limiting

Prevent message flooding:

```python
settings = GatewaySettings(
    rate_limit={
        "per_minute": 100,  # Max 100 messages/minute per agent
        "per_hour": 1000    # Max 1000 messages/hour per agent
    }
)

# If agent exceeds limits, gateway rejects message
# Agent receives error: "Rate limit exceeded"
```

#### Data Loss Prevention (DLP)

Detect and block sensitive data:

```python
settings = GatewaySettings(
    features={"dlp": True}
)

# Gateway scans all messages for:
# - PII (Social Security Numbers, emails, phone numbers)
# - PHI (Medical Record Numbers, diagnosis codes)
# - PCI (Credit card numbers)
# - Secrets (API keys, AWS credentials)

# Messages with violations are:
# - Blocked (rejected)
# - Redacted (sensitive data masked)
# - Alerted (logged for review)
```

### Audit Logging

Gateway creates an immutable audit trail:

```python
# Query audit logs
audit = gateway.audit

# Get messages sent by specific agent
messages = await audit.query_by_sender(
    sender_id="agent_a",
    start_time=1704067200.0,  # Unix timestamp
    end_time=1706745600.0
)

# Get messages to specific agent
messages = await audit.query_by_target(
    target_id="agent_b",
    start_time=1704067200.0,
    end_time=1706745600.0
)

# Get all audit entries
all_entries = await audit.query_all(count=1000)

# Export for compliance
report = await audit.export_compliance_report(
    start_time=1704067200.0,
    end_time=1735689600.0,
    format="csv"  # or "json"
)
```

### Compliance

Gateway mode enables compliance with:

#### HIPAA (Healthcare)
- ✅ Complete audit trail of all PHI access
- ✅ PHI detection and prevention (DLP)
- ✅ Access controls (authentication + authorization)
- ✅ Integrity controls (message signing)
- ✅ 6-year retention support

#### SOC2 (Security)
- ✅ Audit logging for all security events
- ✅ Access control enforcement
- ✅ Rate limiting prevents availability issues
- ✅ Circuit breakers for reliability

#### GDPR (Privacy)
- ✅ Audit trail for data processing
- ✅ DLP prevents unauthorized disclosure
- ✅ Access controls for data protection
- ✅ Data deletion capability

#### PCI-DSS (Payment)
- ✅ Credit card detection and blocking
- ✅ No storage of full PAN
- ✅ Audit trail of payment-related messages
- ✅ Network segmentation (gateway isolation)

### Performance Considerations

Gateway mode adds overhead:

| Metric | P2P Mode | Gateway Mode |
|--------|----------|--------------|
| Latency (P50) | <5ms | 10-15ms |
| Latency (P99) | <10ms | 50-80ms |
| Throughput (single instance) | 10,000+ msg/s | 2,000-5,000 msg/s |
| Throughput (clustered) | N/A | 6,000-9,000 msg/s |

**Optimization tips:**
- Use gateway for sensitive operations only
- Cache authentication results
- Batch operations when possible
- Scale horizontally (multiple gateway instances)
- Use async DLP for non-critical scanning

---

## Advanced Patterns

### Multi-Tier Agent Systems

Build hierarchical agent systems:

```python
# Coordinator agent
class CoordinatorAgent(Agent):
    async def on_start(self):
        # Discover workers
        self.workers = await self.discover(capabilities=["worker"])
        print(f"Found {len(self.workers)} workers")
    
    async def on_message(self, message: AgentMessage):
        if message.payload.get("type") == "job":
            # Distribute work to workers
            job_data = message.payload["data"]
            tasks = self.split_job(job_data)
            
            for i, task in enumerate(tasks):
                worker = self.workers[i % len(self.workers)]
                await self.send(worker["id"], {
                    "type": "task",
                    "task": task,
                    "job_id": message.payload["job_id"]
                })

# Worker agent
class WorkerAgent(Agent):
    async def on_message(self, message: AgentMessage):
        if message.payload.get("type") == "task":
            task = message.payload["task"]
            result = await self.process_task(task)
            
            # Send result back to coordinator
            await self.send(message.sender_id, {
                "type": "result",
                "result": result,
                "job_id": message.payload["job_id"]
            })
```

### Consensus Patterns

Agents voting on decisions:

```python
class VotingAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.votes = {}
    
    async def request_vote(self, proposal_id: str, proposal: dict):
        # Get all voting agents
        voters = await self.discover(capabilities=["voter"])
        
        # Request votes
        for voter in voters:
            await self.send(voter["id"], {
                "type": "vote_request",
                "proposal_id": proposal_id,
                "proposal": proposal
            })
        
        # Wait for votes (simplified)
        await asyncio.sleep(10)
        
        # Tally results
        return self.tally_votes(proposal_id)
    
    async def on_message(self, message: AgentMessage):
        if message.payload.get("type") == "vote_request":
            # Evaluate proposal and cast vote
            vote = await self.evaluate_proposal(message.payload["proposal"])
            
            await self.send(message.sender_id, {
                "type": "vote",
                "proposal_id": message.payload["proposal_id"],
                "vote": vote  # "yes" or "no"
            })
        
        elif message.payload.get("type") == "vote":
            # Record vote
            proposal_id = message.payload["proposal_id"]
            if proposal_id not in self.votes:
                self.votes[proposal_id] = []
            self.votes[proposal_id].append(message.payload["vote"])
```

### Error Handling and Retries

Robust error handling:

```python
class ResilientAgent(Agent):
    async def send_with_retry(
        self,
        target_id: str,
        payload: dict,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ):
        """Send message with automatic retry"""
        for attempt in range(max_retries):
            try:
                await self.send(target_id, payload)
                return  # Success
            except Exception as e:
                if attempt == max_retries - 1:
                    # Last attempt failed
                    logger.error(f"Failed to send after {max_retries} attempts: {e}")
                    raise
                else:
                    # Wait before retry
                    await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
    
    async def request_with_timeout(
        self,
        target_id: str,
        payload: dict,
        timeout: float = 10.0
    ):
        """Request with timeout handling"""
        try:
            response = await self.request(target_id, payload, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Request to {target_id} timed out")
            # Fallback behavior
            return None
    
    async def on_message(self, message: AgentMessage):
        try:
            await self.process_message(message)
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            
            # Send error response if reply expected
            if message.expects_reply:
                await message.reply({
                    "error": str(e),
                    "error_type": type(e).__name__
                })
```

### Health Checks

Monitor agent health:

```python
class MonitorableAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_activity = time.time()
        self.messages_processed = 0
    
    def get_metadata(self) -> dict:
        return {
            "version": "1.0.0",
            "status": "healthy",
            "uptime": time.time() - self.start_time,
            "messages_processed": self.messages_processed
        }
    
    async def on_message(self, message: AgentMessage):
        self.last_activity = time.time()
        self.messages_processed += 1
        
        # Respond to health checks
        if message.payload.get("type") == "health_check":
            await message.reply({
                "status": "healthy",
                "uptime": time.time() - self.start_time,
                "messages_processed": self.messages_processed,
                "last_activity": self.last_activity
            })
        else:
            await self.process_message(message)

# Health checker agent
class HealthChecker(Agent):
    async def check_agent_health(self, agent_id: str) -> dict:
        try:
            response = await self.request(
                agent_id,
                {"type": "health_check"},
                timeout=5.0
            )
            return response.payload
        except asyncio.TimeoutError:
            return {"status": "unhealthy", "reason": "timeout"}
        except Exception as e:
            return {"status": "unhealthy", "reason": str(e)}
```

---

## Best Practices

### Agent Design

#### Keep Agents Focused
Each agent should have a single, well-defined responsibility:

```python
# Good: Focused agent
class TranslationAgent(Agent):
    """Translates text between languages"""
    capabilities = ["translation"]

# Avoid: Agent doing too much
class SuperAgent(Agent):
    """Does translation, sentiment analysis, summarization, and image processing"""
    capabilities = ["translation", "sentiment", "summarization", "image"]
```

#### Use Descriptive IDs
Agent IDs should be meaningful:

```python
# Good
agent = Agent("user_auth_service", capabilities=["authentication"])
agent = Agent("payment_processor_v2", capabilities=["payment"])

# Avoid
agent = Agent("agent1", capabilities=["stuff"])
agent = Agent("a", capabilities=["work"])
```

#### Register Relevant Capabilities
Capabilities should accurately describe what the agent does:

```python
# Good
agent = Agent("nlp_agent", capabilities=["nlp", "sentiment", "entity_extraction"])

# Too vague
agent = Agent("nlp_agent", capabilities=["ai"])

# Too specific (makes discovery harder)
agent = Agent("nlp_agent", capabilities=["bert_sentiment_analysis_v2_fine_tuned"])
```

### Message Design

#### Use Structured Payloads
Include message type and versioning:

```python
# Good
await agent.send("target", {
    "type": "task_request",
    "version": "1.0",
    "task": {
        "operation": "translate",
        "text": "Hello world",
        "source_lang": "en",
        "target_lang": "es"
    }
})

# Avoid: Flat, unstructured payloads
await agent.send("target", {
    "text": "Hello world",
    "lang": "es"
})
```

#### Include Correlation IDs
For complex workflows, include correlation IDs:

```python
await agent.send("worker", {
    "type": "task",
    "correlation_id": str(uuid.uuid4()),
    "job_id": "job_123",
    "task_id": "task_456",
    "data": {...}
})
```

#### Validate Incoming Messages
Always validate message structure:

```python
async def on_message(self, message: AgentMessage):
    # Validate message type
    msg_type = message.payload.get("type")
    if not msg_type:
        logger.warning(f"Message missing type from {message.sender_id}")
        return
    
    # Route by type
    if msg_type == "task":
        if "task_data" not in message.payload:
            logger.error("Task message missing task_data")
            return
        await self.handle_task(message)
    elif msg_type == "query":
        await self.handle_query(message)
    else:
        logger.warning(f"Unknown message type: {msg_type}")
```

### State Management

#### Keep State Minimal
Only store what's necessary:

```python
# Good: Minimal state
await agent.update_state({
    "current_task_id": "task_123",
    "tasks_completed": 42
})

# Avoid: Storing everything
await agent.update_state({
    "all_messages_ever_received": [...],  # Use database instead
    "complete_task_history": [...],        # Use external storage
    "cached_responses": {...}              # Use Redis cache directly
})
```

#### Use State for Persistence Only
Don't use state for runtime-only data:

```python
class MyAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Runtime-only: Use instance variables
        self.active_connections = []
        self.temp_cache = {}
    
    async def on_start(self):
        # Persistent: Use state
        await self.update_state({
            "total_processed": 0,
            "config_version": "1.0"
        })
```

### Error Handling

#### Always Handle on_message Errors
Never let exceptions escape:

```python
async def on_message(self, message: AgentMessage):
    try:
        await self.process_message(message)
    except ValueError as e:
        logger.error(f"Invalid message format: {e}")
        if message.expects_reply:
            await message.reply({"error": "Invalid format"})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        if message.expects_reply:
            await message.reply({"error": "Internal error"})
```

#### Use Timeouts
Always set timeouts for requests:

```python
# Good
try:
    response = await agent.request("slow_agent", {...}, timeout=10.0)
except asyncio.TimeoutError:
    logger.warning("Request timed out, using fallback")
    response = await self.fallback_method()

# Avoid: No timeout (could hang forever)
response = await agent.request("slow_agent", {...})
```

### Performance

#### Batch Operations
When sending multiple messages:

```python
# Good: Send concurrently
tasks = [
    agent.send(worker["id"], task)
    for worker, task in zip(workers, tasks)
]
await asyncio.gather(*tasks)

# Avoid: Sequential sends
for worker, task in zip(workers, tasks):
    await agent.send(worker["id"], task)  # Slow!
```

#### Avoid Blocking Operations
Use async versions of I/O operations:

```python
# Good: Async I/O
async def process_file(self, path: str):
    async with aiofiles.open(path, 'r') as f:
        content = await f.read()
    return await self.process_content(content)

# Avoid: Blocking I/O
def process_file(self, path: str):
    with open(path, 'r') as f:  # Blocks event loop!
        content = f.read()
    return self.process_content(content)
```

### Security (Gateway Mode)

#### Use Gateway for Sensitive Operations
Route sensitive operations through gateway:

```python
# Create agents with gateway mode for sensitive data
payment_agent = Agent(
    "payment_processor",
    use_gateway=True,  # Enable security features
    capabilities=["payment"]
)
```

#### Configure Appropriate Rate Limits
Set rate limits based on expected usage:

```python
settings = GatewaySettings(
    rate_limit={
        "per_minute": 60,   # 1 per second average
        "per_hour": 1000    # Burst allowance
    }
)
```

#### Minimize Sensitive Data in Payloads
Don't send unnecessary PII/PHI:

```python
# Good: Minimal sensitive data
await agent.send("doctor", {
    "patient_id": "hashed_id",  # Hashed, not real ID
    "symptoms": ["fever", "cough"],
    "request": "diagnosis"
})

# Avoid: Unnecessary PII
await agent.send("doctor", {
    "patient_name": "John Doe",      # Not needed
    "patient_ssn": "123-45-6789",    # Definitely not needed!
    "patient_address": "123 Main St", # Not needed
    "symptoms": ["fever", "cough"]
})
```

---

## Deployment

### Local Development

For development, run Redis locally:

```bash
# macOS
brew install redis
brew services start redis

# Docker
docker run -d -p 6379:6379 redis:latest
```

Start your agents:

```bash
# Single agent
uv run python my_agent.py

# Multiple agents
uv run python agent_a.py &
uv run python agent_b.py &
```

### Production Deployment

#### Using Docker Compose

`docker-compose.yml`:

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
  
  gateway:
    build: .
    command: python -m mas.gateway
    environment:
      - REDIS_URL=redis://redis:6379
      - GATEWAY_RATE_LIMIT__PER_MINUTE=100
      - GATEWAY_RATE_LIMIT__PER_HOUR=1000
    depends_on:
      - redis
  
  agent_worker:
    build: .
    command: python worker_agent.py
    environment:
      - REDIS_URL=redis://redis:6379
      - USE_GATEWAY=true
    depends_on:
      - gateway
    deploy:
      replicas: 3

volumes:
  redis_data:
```

#### Kubernetes Deployment

`deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: worker-agent
spec:
  replicas: 3
  selector:
    matchLabels:
      app: worker-agent
  template:
    metadata:
      labels:
        app: worker-agent
    spec:
      containers:
      - name: worker
        image: myregistry/worker-agent:latest
        env:
        - name: REDIS_URL
          value: "redis://redis-service:6379"
        - name: USE_GATEWAY
          value: "true"
        - name: AGENT_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
---
apiVersion: v1
kind: Service
metadata:
  name: redis-service
spec:
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
```

### Environment Variables

Configure agents via environment variables:

```python
import os

agent = Agent(
    agent_id=os.getenv("AGENT_ID", "default_agent"),
    redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
    use_gateway=os.getenv("USE_GATEWAY", "false").lower() == "true"
)
```

### High Availability

#### Redis Clustering

Use Redis Cluster for high availability:

```python
from redis.cluster import RedisCluster

# Configure cluster nodes
startup_nodes = [
    {"host": "redis-node-1", "port": 6379},
    {"host": "redis-node-2", "port": 6379},
    {"host": "redis-node-3", "port": 6379},
]

agent = Agent(
    "my_agent",
    redis_url="redis://redis-node-1:6379"  # Will discover cluster
)
```

#### Agent Redundancy

Run multiple instances of critical agents:

```python
import socket

# Each instance gets unique ID
hostname = socket.gethostname()
agent = Agent(
    f"worker_{hostname}",  # e.g., "worker_pod_123"
    capabilities=["worker"]  # Same capability for load balancing
)
```

### Monitoring

#### Logging

Use structured logging:

```python
import logging
import json

# Configure structured logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class MyAgent(Agent):
    async def on_message(self, message: AgentMessage):
        logger.info(
            "Message processed",
            extra={
                "agent_id": self.id,
                "sender_id": message.sender_id,
                "message_type": message.payload.get("type"),
                "message_id": message.message_id
            }
        )
```

#### Metrics

Track agent metrics:

```python
from prometheus_client import Counter, Histogram, start_http_server

messages_received = Counter('agent_messages_received', 'Messages received', ['agent_id'])
messages_sent = Counter('agent_messages_sent', 'Messages sent', ['agent_id'])
message_processing_time = Histogram('agent_message_processing_seconds', 'Time to process message')

class MeteredAgent(Agent):
    async def on_message(self, message: AgentMessage):
        messages_received.labels(agent_id=self.id).inc()
        
        with message_processing_time.time():
            await self.process_message(message)
    
    async def send(self, target_id: str, payload: dict):
        await super().send(target_id, payload)
        messages_sent.labels(agent_id=self.id).inc()

# Start metrics server
start_http_server(8000)  # Metrics at http://localhost:8000/metrics
```

---

## Troubleshooting

### Connection Issues

#### "Connection refused" Error

**Problem**: Can't connect to Redis

**Solutions**:
```bash
# Check if Redis is running
redis-cli ping  # Should return "PONG"

# Start Redis if not running
brew services start redis  # macOS
docker start redis  # Docker

# Check Redis port
netstat -an | grep 6379

# Test connection
redis-cli -h localhost -p 6379 ping
```

#### "Agent not found" Error

**Problem**: Target agent not registered

**Solutions**:
```python
# Verify agent is registered
agents = await agent.discover()
print(f"Active agents: {[a['id'] for a in agents]}")

# Check agent started
# Make sure target agent called await agent.start()

# Check agent heartbeat
# Agent may have timed out (60s without heartbeat)
```

### Message Issues

#### Messages Not Being Received

**Checklist**:
1. Target agent is started: `await target_agent.start()`
2. Target agent has `on_message` implemented
3. Correct agent ID used in `send()`
4. No exceptions in `on_message` (check logs)
5. Redis pub/sub working: `redis-cli PUBSUB CHANNELS agent.*`

**Debug**:
```python
class DebugAgent(Agent):
    async def on_message(self, message: AgentMessage):
        print(f"DEBUG: Received message from {message.sender_id}")
        print(f"DEBUG: Payload: {message.payload}")
        try:
            await self.process_message(message)
        except Exception as e:
            print(f"DEBUG: Error processing: {e}")
            raise
```

#### Request Timeouts

**Problem**: `request()` times out waiting for response

**Solutions**:
```python
# 1. Increase timeout
response = await agent.request("slow_agent", {...}, timeout=60.0)

# 2. Check responder implements reply
class ResponderAgent(Agent):
    async def on_message(self, message: AgentMessage):
        if message.expects_reply:
            await message.reply({"result": "success"})  # Don't forget this!

# 3. Check for errors in responder
# Look at responder agent logs for exceptions
```

### State Issues

#### State Not Persisting

**Problem**: State not saved after restart

**Checklist**:
1. Called `await agent.update_state({...})`
2. Used same agent ID on restart
3. Redis is persistent (not in-memory only)
4. No errors in `update_state` call

**Verify state in Redis**:
```bash
# Check state exists
redis-cli HGETALL agent.state:my_agent

# Check agent metadata
redis-cli HGETALL agent:my_agent
```

#### State Update Errors

**Problem**: `update_state` throws validation error (Pydantic)

**Solution**:
```python
# Check state model matches updates
class MyState(BaseModel):
    counter: int  # Must be int

# This will fail:
await agent.update_state({"counter": "not_a_number"})

# This works:
await agent.update_state({"counter": 42})
```

### Gateway Issues

#### "Authentication failed"

**Problem**: Agent can't authenticate with gateway

**Solutions**:
```python
# 1. Verify gateway is started before agent
await gateway.start()
await agent.start()  # Agent gets token on start

# 2. Check agent has gateway reference
agent.set_gateway(gateway)

# 3. Verify token in agent
print(f"Agent token: {agent.token}")
```

#### "Authorization denied"

**Problem**: Agent not allowed to message target

**Solutions**:
```python
# Grant permission
from mas.gateway import AuthManager

auth_manager = gateway.auth_manager
await auth_manager.grant_permission(
    agent_id="sender_agent",
    target_id="target_agent",
    action="send"
)

# Check permissions
can_send = await auth_manager.check_permission(
    agent_id="sender_agent",
    target_id="target_agent",
    action="send"
)
print(f"Can send: {can_send}")
```

#### "Rate limit exceeded"

**Problem**: Agent sending too many messages

**Solutions**:
```python
# 1. Increase rate limits
settings = GatewaySettings(
    rate_limit={
        "per_minute": 200,  # Increase from 100
        "per_hour": 2000    # Increase from 1000
    }
)

# 2. Add backoff in agent
async def send_with_backoff(self, target_id: str, payload: dict):
    try:
        await self.send(target_id, payload)
    except Exception as e:
        if "rate limit" in str(e).lower():
            await asyncio.sleep(1)  # Wait before retry
            await self.send(target_id, payload)

# 3. Batch messages
# Send one message with multiple items instead of many messages
```

### Performance Issues

#### High Latency

**Symptoms**: Messages taking too long to arrive

**Solutions**:
```python
# 1. Check network latency to Redis
# Use redis-cli --latency

# 2. Use P2P mode instead of gateway
agent = Agent("my_agent", use_gateway=False)

# 3. Check message size
# Large payloads slow down serialization
import sys
payload_size = sys.getsizeof(json.dumps(payload))
print(f"Payload size: {payload_size} bytes")

# 4. Check CPU usage
# Gateway DLP scanning is CPU-intensive
# Consider disabling for non-sensitive messages
```

#### Low Throughput

**Symptoms**: Can't send many messages per second

**Solutions**:
```python
# 1. Send messages concurrently
tasks = [agent.send(target, payload) for _ in range(100)]
await asyncio.gather(*tasks)

# 2. Use connection pooling (already enabled by default)

# 3. Scale horizontally
# Run multiple agent instances

# 4. Check Redis performance
# redis-cli INFO stats
```

---

## API Reference

### Agent Class

```python
class Agent:
    def __init__(
        self,
        agent_id: str,
        capabilities: list[str] | None = None,
        redis_url: str = "redis://localhost:6379",
        state_model: type[BaseModel] | None = None,
        use_gateway: bool = False,
        gateway_url: str | None = None
    )
```

**Parameters:**
- `agent_id`: Unique identifier for the agent
- `capabilities`: List of capability tags for discovery
- `redis_url`: Redis connection URL
- `state_model`: Optional Pydantic model for typed state
- `use_gateway`: Enable gateway mode (default: False)
- `gateway_url`: Gateway service URL (if different from redis_url)

**Properties:**
- `id: str` - Agent identifier
- `capabilities: list[str]` - Agent capabilities
- `state: dict | BaseModel` - Current agent state
- `token: str | None` - Authentication token (gateway mode)

**Methods:**

#### `async start() -> None`
Start the agent. Must be called before sending/receiving messages.

```python
agent = Agent("my_agent")
await agent.start()
```

#### `async stop() -> None`
Stop the agent and cleanup resources.

```python
await agent.stop()
```

#### `async send(target_id: str, payload: dict) -> None`
Send a message to another agent (fire-and-forget).

```python
await agent.send("target_agent", {
    "action": "process",
    "data": {"key": "value"}
})
```

**Parameters:**
- `target_id`: Target agent identifier
- `payload`: Message payload (must be JSON-serializable)

**Raises:**
- `RuntimeError`: If agent not started or gateway rejects message

#### `async request(target_id: str, payload: dict, timeout: float = 30.0) -> AgentMessage`
Send a request and wait for response.

```python
response = await agent.request(
    "calculator_agent",
    {"operation": "add", "numbers": [1, 2, 3]},
    timeout=10.0
)
result = response.payload["result"]
```

**Parameters:**
- `target_id`: Target agent identifier
- `payload`: Request payload
- `timeout`: Maximum wait time in seconds (default: 30.0)

**Returns:**
- `AgentMessage`: Response message from target agent

**Raises:**
- `asyncio.TimeoutError`: If no response within timeout
- `RuntimeError`: If agent not started

#### `async discover(capabilities: list[str] | None = None) -> list[dict]`
Discover agents by capabilities.

```python
# Find all agents
all_agents = await agent.discover()

# Find agents with specific capabilities
nlp_agents = await agent.discover(capabilities=["nlp"])
```

**Parameters:**
- `capabilities`: List of required capabilities (optional). If None, returns all active agents.

**Returns:**
- `list[dict]`: List of agent information dictionaries with keys:
  - `id`: Agent identifier
  - `capabilities`: List of capabilities
  - `metadata`: Agent metadata dictionary

#### `async update_state(updates: dict) -> None`
Update agent state (automatically persisted to Redis).

```python
await agent.update_state({
    "counter": 42,
    "status": "active"
})
```

**Parameters:**
- `updates`: Dictionary of state updates

**Raises:**
- `RuntimeError`: If agent not started
- `ValidationError`: If state model validation fails (typed state)

#### `async reset_state() -> None`
Reset agent state to defaults.

```python
await agent.reset_state()
```

#### `set_gateway(gateway: GatewayService) -> None`
Set gateway instance for message routing (required for gateway mode).

```python
gateway = GatewayService()  # Uses default redis://localhost:6379
agent.set_gateway(gateway)
```

**Parameters:**
- `gateway`: GatewayService instance

#### Lifecycle Hooks

Override these methods to customize agent behavior:

##### `async on_start() -> None`
Called when agent starts. Override for initialization logic.

```python
class MyAgent(Agent):
    async def on_start(self):
        print("Agent starting...")
        await self.update_state({"initialized": True})
```

##### `async on_stop() -> None`
Called when agent stops. Override for cleanup logic.

```python
class MyAgent(Agent):
    async def on_stop(self):
        print("Agent stopping...")
        # Close connections, save data, etc.
```

##### `async on_message(message: AgentMessage) -> None`
Called when message received. Override to handle messages.

```python
class MyAgent(Agent):
    async def on_message(self, message: AgentMessage):
        print(f"Received: {message.payload}")
        # Process message
```

**Parameters:**
- `message`: Received AgentMessage

##### `get_metadata() -> dict`
Override to provide custom metadata for discovery.

```python
class MyAgent(Agent):
    def get_metadata(self) -> dict:
        return {
            "version": "1.0.0",
            "model": "gpt-4",
            "region": "us-east-1"
        }
```

**Returns:**
- `dict`: Metadata dictionary

---

### AgentMessage Class

```python
class AgentMessage:
    sender_id: str
    target_id: str
    payload: dict
    timestamp: float
    message_id: str
```

**Properties:**
- `sender_id: str` - ID of sender agent
- `target_id: str` - ID of target agent
- `payload: dict` - Message content
- `timestamp: float` - Unix timestamp when message was created
- `message_id: str` - Unique message identifier
- `expects_reply: bool` - Whether message expects a reply
- `is_reply: bool` - Whether message is a reply to a request

**Methods:**

#### `async reply(payload: dict) -> None`
Reply to this message (request-response pattern).

```python
async def on_message(self, message: AgentMessage):
    if message.expects_reply:
        result = await self.process(message.payload)
        await message.reply({"result": result})
```

**Parameters:**
- `payload`: Reply payload

**Raises:**
- `RuntimeError`: If message doesn't expect reply or agent not available

---

### GatewayService Class

```python
class GatewayService:
    def __init__(
        self,
        settings: GatewaySettings | None = None
    )
```

**Parameters:**
- `settings`: Optional GatewaySettings instance (uses production-ready defaults if None)

**Methods:**

#### `async start() -> None`
Start the gateway service.

```python
gateway = GatewayService(redis_url="redis://localhost")
await gateway.start()
```

#### `async stop() -> None`
Stop the gateway service.

```python
await gateway.stop()
```

#### `async handle_message(message: AgentMessage, token: str) -> GatewayResult`
Process a message through gateway (called internally by agents).

**Parameters:**
- `message`: AgentMessage to process
- `token`: Agent authentication token

**Returns:**
- `GatewayResult`: Result with success status and decision

---

### GatewaySettings Class

```python
class GatewaySettings:
    redis: RedisSettings
    rate_limit: RateLimitSettings
    features: FeaturesSettings
    circuit_breaker: CircuitBreakerSettings
    priority_queue: PriorityQueueSettings
    message_signing: MessageSigningSettings
```

**Methods:**

#### `from_yaml(path: str) -> GatewaySettings`
Load settings from YAML file.

```python
settings = GatewaySettings.from_yaml("gateway.yaml")
```

#### `to_yaml(path: str) -> None`
Export settings to YAML file.

```python
settings.to_yaml("gateway-export.yaml")
```

#### `summary() -> str`
Get human-readable summary of settings.

```python
print(settings.summary())
```

---

## Examples

See the `examples/` directory for complete working examples:

### Chemistry Tutoring (Peer-to-Peer)
`examples/chemistry_tutoring/`

Two agents (student and professor) having an educational conversation using OpenAI.

**Demonstrates:**
- Peer-to-peer messaging
- Agent discovery
- Request-response pattern
- OpenAI integration

**Run:**
```bash
cd examples/chemistry_tutoring
./run.sh
```

### Healthcare Consultation (Gateway Mode)
`examples/healthcare_consultation/`

Three agents (patient, GP doctor, specialist) in a healthcare workflow with full security.

**Demonstrates:**
- Gateway mode with all security features
- Multi-tier agent communication
- Authentication and authorization
- Rate limiting and DLP
- Audit logging
- HIPAA compliance

**Run:**
```bash
cd examples/healthcare_consultation
./run.sh
```

---

## Additional Resources

- **[Architecture Guide](ARCHITECTURE.md)** - Internal architecture and design decisions
- **[Gateway Guide](GATEWAY.md)** - Detailed gateway architecture and security features
- **[Issue Tracking](AGENTS.md)** - Development workflow and issue tracking with bd (beads)
- **[Examples](examples/)** - Complete working examples

---

## Getting Help

### Common Questions

**Q: How do I run multiple agents on different machines?**

A: All agents connect to the same Redis instance. Just use the same `redis_url`:

```python
# Machine 1
agent_a = Agent("agent_a", redis_url="redis://shared-redis:6379")

# Machine 2  
agent_b = Agent("agent_b", redis_url="redis://shared-redis:6379")
```

**Q: Can agents have the same capabilities?**

A: Yes! Multiple agents can share capabilities for load balancing:

```python
worker_1 = Agent("worker_1", capabilities=["worker"])
worker_2 = Agent("worker_2", capabilities=["worker"])

# Both will be discovered when searching for "worker"
workers = await agent.discover(capabilities=["worker"])
```

**Q: What happens if an agent crashes?**

A: The agent's registration expires after 60 seconds (heartbeat timeout). Other agents will stop discovering it. State remains in Redis and can be restored on restart.

**Q: How do I handle agent versioning?**

A: Include version in metadata:

```python
class MyAgent(Agent):
    def get_metadata(self) -> dict:
        return {"version": "2.0.0"}

# Discoverers can filter by version
agents = await agent.discover(capabilities=["nlp"])
v2_agents = [a for a in agents if a["metadata"].get("version") == "2.0.0"]
```

**Q: Can I use MAS Framework with other message brokers?**

A: Currently only Redis is supported. The framework is designed around Redis primitives (pub/sub, streams, hashes).

### Reporting Issues

Found a bug or have a feature request? Open an issue on GitHub:

https://github.com/yourusername/mas-framework/issues

### Contributing

Contributions are welcome! See [AGENTS.md](AGENTS.md) for development workflow.

---

**License**: MIT License - see [LICENSE](LICENSE) file for details

**Version**: See [src/mas/__version__.py](src/mas/__version__.py) for current version
