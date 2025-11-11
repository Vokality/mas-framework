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

- **Gateway Messaging**: Centralized routing with security and compliance
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

Here's a simple agent using a decorator-based handler:

```python
import asyncio
from pydantic import BaseModel
from mas import Agent, AgentMessage

class HelloRequest(BaseModel):
    text: str

class HelloAgent(Agent):
    @Agent.on("hello.message", model=HelloRequest)
    async def handle_hello(self, message: AgentMessage, payload: HelloRequest):
        print(f"Received: {payload.text}")

async def main():
    agent = HelloAgent("hello_agent", capabilities=["greeting"])
    await agent.start()
    await agent.send("other_agent", "hello.message", {"text": "Hello, world!"})
    await asyncio.sleep(60)
    await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Two Agents Communicating

Let's create two agents that talk to each other:

```python
import asyncio, time
from pydantic import BaseModel
from mas import Agent, AgentMessage

class Greeting(BaseModel):
    greeting: str
    timestamp: float

class Reply(BaseModel):
    reply: str
    received_at: float

class SenderAgent(Agent):
    async def on_start(self):
        print(f"{self.id} is ready!")
        agents = await self.discover(capabilities=["receiver"])
        if agents:
            receiver_id = agents[0]["id"]
            await self.send(receiver_id, "greeting.message", Greeting(greeting="Hello from sender!", timestamp=time.time()).model_dump())

class ReceiverAgent(Agent):
    @Agent.on("greeting.message", model=Greeting)
    async def handle_greeting(self, message: AgentMessage, payload: Greeting):
        print(f"Received from {message.sender_id}: {payload.greeting}")
        await message.reply("greeting.reply", Reply(reply="Hello back!", received_at=time.time()).model_dump())

async def main():
    receiver = ReceiverAgent("receiver_agent", capabilities=["receiver"])
    await receiver.start()
    sender = SenderAgent("sender_agent", capabilities=["sender"])
    await sender.start()
    await asyncio.sleep(5)
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
```

---

## Messaging Patterns

### Fire-and-Forget (Send)

Send a message without waiting for a response:

```python
# Sender
await agent.send("target_agent", "process.task", {
    "action": "process",
    "data": [1, 2, 3]
})

# Receiver - using decorator-based handler
class ProcessorAgent(Agent):
    @Agent.on("process.task")
    async def handle_process(self, message: AgentMessage, payload: None):
        data = message.data["data"]
        result = self.process(data)
        # Do something with result
```

### Request-Response

Send a message and wait for a response:

```python
from pydantic import BaseModel

class CalculationRequest(BaseModel):
    operation: str
    numbers: list[float]

class CalculationResponse(BaseModel):
    result: float

# Requester
response = await agent.request(
    "calculator_agent",
    "calculation.request",
    CalculationRequest(operation="add", numbers=[1, 2, 3]).model_dump(),
    timeout=10.0  # Wait up to 10 seconds
)
result = response.data["result"]
print(f"Result: {result}")

# Responder - using decorator-based handler
class CalculatorAgent(Agent):
    @Agent.on("calculation.request", model=CalculationRequest)
    async def handle_calculation(self, message: AgentMessage, payload: CalculationRequest):
        if payload.operation == "add":
            result = sum(payload.numbers)
        elif payload.operation == "multiply":
            result = 1
            for n in payload.numbers:
                result *= n
        else:
            raise ValueError(f"Unknown operation: {payload.operation}")
        
        # Send reply using message.reply()
        await message.reply(
            "calculation.response",
            CalculationResponse(result=result).model_dump()
        )
```

**Key Points:**
- `request()` automatically handles correlation IDs
- Responder uses `message.reply(message_type, payload)` to send response
- `message.expects_reply` tells responder a reply is expected
- Timeout is configurable (default: 30 seconds)
- Use Pydantic models for type-safe request/response payloads

### Broadcast Pattern

Send a message to all agents with a specific capability:

```python
async def broadcast_to_workers(self):
    # Discover all workers
    workers = await self.discover(capabilities=["worker"])
    
    # Send task to each worker
    for worker in workers:
        await self.send(worker["id"], "task.assign", {
            "task": "process_chunk",
            "chunk_id": worker["id"]
        })
```

### Pub-Sub Pattern

Multiple agents listen for specific message types:

```python
class SubscriberAgent(Agent):
    @Agent.on("notification.message")
    async def handle_notification(self, message: AgentMessage, payload: None):
        await self.handle_notification(message.data)
    
    @Agent.on("alert.message")
    async def handle_alert(self, message: AgentMessage, payload: None):
        await self.handle_alert(message.data)

# Publisher
class PublisherAgent(Agent):
    async def publish_notification(self, text: str):
        # Get all subscribers
        subscribers = await self.discover(capabilities=["subscriber"])
        
        # Send to each
        for sub in subscribers:
            await self.send(sub["id"], "notification.message", {
                "text": text
            })
```

### Pipeline Pattern

Chain agents together for multi-step processing:

```python
from pydantic import BaseModel

class StageOneRequest(BaseModel):
    data: dict

class StageTwoRequest(BaseModel):
    data: dict
    stage: int

class StageOneAgent(Agent):
    """First stage of pipeline"""
    @Agent.on("stage1.process", model=StageOneRequest)
    async def handle_stage1(self, message: AgentMessage, payload: StageOneRequest):
        input_data = payload.data
        processed = self.stage_one_processing(input_data)
        stage_two = await self.discover(capabilities=["stage_two"])
        if stage_two:
            await self.send(stage_two[0]["id"], "stage2.process", {"data": processed, "stage": 2})

class StageTwoAgent(Agent):
    """Second stage of pipeline"""
    @Agent.on("stage2.process", model=StageTwoRequest)
    async def handle_stage2(self, message: AgentMessage, payload: StageTwoRequest):
        if payload.stage == 2:
            data = payload.data
            final_result = self.stage_two_processing(data)
            await message.reply("pipeline.complete", {"result": final_result, "completed": True})
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
from pydantic import BaseModel, Field
from mas import Agent, AgentMessage

class MyAgentState(BaseModel):
    counter: int = Field(default=0)
    status: str = Field(default="idle")

class StatefulAgent(Agent[MyAgentState]):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(agent_id, state_model=MyAgentState, **kwargs)

    async def on_start(self):
        # State is automatically loaded from Redis
        print(f"Current state: {self.state}")
        # Initialize state (automatically persisted to Redis)
        await self.update_state({"counter": 0, "status": "active"})

    @Agent.on("counter.increment")
    async def handle_increment(self, message: AgentMessage, payload: None):
        self.state.counter += 1
        await self.update_state({"counter": self.state.counter})
        print(f"Processed {self.state.counter} messages")
```

### State Persistence

State automatically persists across restarts:

```python
from pydantic import BaseModel
from mas import Agent

class MyState(BaseModel):
    counter: int = 0
    name: str = "Alice"

class MyAgent(Agent[MyState]):
    def __init__(self, agent_id: str, **kwargs):
        super().__init__(agent_id, state_model=MyState, **kwargs)

# First run
agent = MyAgent("my_agent")
await agent.start()
await agent.update_state({"counter": 42})
await agent.stop()

# Second run (after restart)
agent = MyAgent("my_agent")  # Same ID!
await agent.start()
print(agent.state.counter)  # 42
print(agent.state.name)     # "Alice"
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

    @Agent.on("item.append")
    async def handle_item(self, message: AgentMessage, payload: dict):
        self.state.counter += 1
        self.state.items.append(payload["item"])
        await self.update_state({"counter": self.state.counter, "items": self.state.items})
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

## Messaging

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

The gateway adds security and reliability features that introduce modest overhead. Optimize by caching, batching, and horizontal scaling when needed.

---

## Advanced Patterns

### Multi-Tier Agent Systems

Build hierarchical agent systems:

```python
# Coordinator agent
class CoordinatorAgent(Agent):
    async def on_start(self):
        self.workers = await self.discover(capabilities=["worker"])
        print(f"Found {len(self.workers)} workers")

    @Agent.on("job.submit")
    async def handle_job(self, message: AgentMessage, payload: dict):
        tasks = self.split_job(payload["data"])
        for i, task in enumerate(tasks):
            worker = self.workers[i % len(self.workers)]
            await self.send(worker["id"], "task.process", {"task": task, "job_id": payload["job_id"]})

# Worker agent
class WorkerAgent(Agent):
    @Agent.on("task.process")
    async def handle_task(self, message: AgentMessage, payload: dict):
        result = await self.process_task(payload["task"])
        await message.reply("task.result", {"result": result, "job_id": payload["job_id"]})
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
    
    @Agent.on("vote.request")
    async def handle_vote_request(self, message: AgentMessage, payload: dict):
        vote = await self.evaluate_proposal(payload["proposal"])
        await message.reply("vote.response", {"proposal_id": payload["proposal_id"], "vote": vote})
    
    @Agent.on("vote.response")
    async def record_vote(self, message: AgentMessage, payload: dict):
        proposal_id = payload["proposal_id"]
        if proposal_id not in self.votes:
            self.votes[proposal_id] = []
        self.votes[proposal_id].append(payload["vote"])
```

### Error Handling and Retries

Robust error handling:

```python
class ResilientAgent(Agent):
    async def send_with_retry(self, target_id: str, message_type: str, payload: dict, max_retries: int = 3, retry_delay: float = 1.0):
        """Send message with automatic retry"""
        for attempt in range(max_retries):
            try:
                await self.send(target_id, message_type, payload)
                return  # Success
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to send after {max_retries} attempts: {e}")
                    raise
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff

    async def request_with_timeout(self, target_id: str, message_type: str, payload: dict, timeout: float = 10.0):
        """Request with timeout handling"""
        try:
            response = await self.request(target_id, message_type, payload, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Request to {target_id} timed out")
            return None

    @Agent.on("task.process", model=TaskRequest)
    async def handle_task(self, message: AgentMessage, payload: TaskRequest):
        try:
            await self.process_task(payload)
            await message.reply("task.complete", {"status": "done"})
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            if message.expects_reply:
                await message.reply("task.error", {"error": "Internal error"})
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
    
    @Agent.on("health_check.request")
    async def handle_health(self, message: AgentMessage, payload: None):
        self.last_activity = time.time()
        self.messages_processed += 1
        await message.reply("health_check.response", {
            "status": "healthy",
            "uptime": time.time() - self.start_time,
            "messages_processed": self.messages_processed,
            "last_activity": self.last_activity
        })

# Health checker agent
class HealthChecker(Agent):
    async def check_agent_health(self, agent_id: str) -> dict:
        try:
            response = await self.request(
                agent_id,
                "health_check.request",
                {},
                timeout=5.0
            )
            return response.data
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
@Agent.on("task.process", model=TaskRequest)
async def handle_task(self, message: AgentMessage, payload: TaskRequest):
    # Validated payload; process task
    await message.reply("task.complete", {"status": "done"})
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

#### Handler Error Handling
Never let exceptions escape your decorator-based handlers:

```python
@Agent.on("task.process", model=TaskRequest)
async def handle_task(self, message: AgentMessage, payload: TaskRequest):
    try:
        await self.process_task(payload)
        await message.reply("task.complete", {"status": "done"})
    except ValueError:
        await message.reply("task.error", {"error": "Invalid format"})
    except Exception:
        logger.exception("Unexpected error")
        await message.reply("task.error", {"error": "Internal error"})
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
from pydantic import BaseModel
from mas import Agent, AgentMessage

# Configure structured logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class AuditEvent(BaseModel):
    action: str

class MyAgent(Agent):
    @Agent.on("audit.event", model=AuditEvent)
    async def handle_audit(self, message: AgentMessage, payload: AuditEvent):
        logger.info(
            "Message processed",
            extra={
                "agent_id": self.id,
                "sender_id": message.sender_id,
                "message_type": message.message_type,
                "message_id": message.message_id,
                "action": payload.action,
            }
        )
```

#### Metrics

Track agent metrics:

```python
from prometheus_client import Counter, Histogram, start_http_server
from pydantic import BaseModel
from mas import Agent, AgentMessage

messages_received = Counter('agent_messages_received', 'Messages received', ['agent_id'])
messages_sent = Counter('agent_messages_sent', 'Messages sent', ['agent_id'])
message_processing_time = Histogram('agent_message_processing_seconds', 'Time to process message')

class TaskRequest(BaseModel):
    task: str

class MeteredAgent(Agent):
    @Agent.on("task.process", model=TaskRequest)
    async def handle_task(self, message: AgentMessage, payload: TaskRequest):
        messages_received.labels(agent_id=self.id).inc()
        with message_processing_time.time():
            await self.process_task(payload.task)
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
2. Target agent has a matching `@Agent.on("<message.type>")` handler registered
3. Correct agent ID used in `send()`
4. No exceptions in handler execution (check logs)
5. Redis Streams present:
   - `redis-cli XINFO STREAM mas.gateway.ingress`
   - `redis-cli XRANGE agent.stream:<target_id> - + COUNT 1`

**Debug**:
```python
class DebugAgent(Agent):
    @Agent.on("debug.message")
    async def handle_debug(self, message: AgentMessage, payload: None):
        print(f"DEBUG: Received message from {message.sender_id}")
        print(f"DEBUG: Payload: {message.data}")
```

#### Request Timeouts

**Problem**: `request()` times out waiting for response

**Solutions**:
```python
# 1. Increase timeout
response = await agent.request("slow_agent", {...}, timeout=60.0)

# 2. Check responder implements reply
class ResponderAgent(Agent):
    @Agent.on("query.request")
    async def handle_query(self, message: AgentMessage, payload: None):
        await message.reply("query.response", {"result": "success"})  # Don't forget this!

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

# 2. Check message size
# Large payloads slow down serialization
import sys
payload_size = sys.getsizeof(json.dumps(payload))
print(f"Payload size: {payload_size} bytes")

# 3. Check CPU usage
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

#### `async send(target_id: str, message_type: str, data: dict) -> None`
Send a message to another agent (fire-and-forget).

```python
await agent.send("target_agent", "process.task", {
    "action": "process",
    "data": {"key": "value"}
})
```

**Parameters:**
- `target_id`: Target agent identifier
- `message_type`: Message type identifier (string)
- `data`: Message payload dictionary (must be JSON-serializable)

**Raises:**
- `RuntimeError`: If agent not started or gateway rejects message

#### `async request(target_id: str, message_type: str, data: dict, timeout: float = 30.0) -> AgentMessage`
Send a request and wait for response.

```python
response = await agent.request(
    "calculator_agent",
    "calculation.request",
    {"operation": "add", "numbers": [1, 2, 3]},
    timeout=10.0
)
result = response.data["result"]
```

**Parameters:**
- `target_id`: Target agent identifier
- `message_type`: Message type identifier (string)
- `data`: Request payload dictionary (must be JSON-serializable)
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

##### Handler Registration
Register handlers exclusively with the `@Agent.on()` decorator.

##### Decorator-Based Message Handlers

Register typed message handlers using the `@Agent.on()` decorator:

```python
from pydantic import BaseModel
from mas import Agent, AgentMessage

class TaskRequest(BaseModel):
    task_id: str
    priority: int = 1

class MyAgent(Agent):
    @Agent.on("task.process", model=TaskRequest)
    async def handle_task(self, message: AgentMessage, payload: TaskRequest):
        """Handle task processing with typed payload"""
        print(f"Processing task {payload.task_id} with priority {payload.priority}")
        # Process task...
        await message.reply("task.complete", {"status": "done"})
    
    @Agent.on("status.check")
    async def handle_status(self, message: AgentMessage, payload: None):
        """Handle status check (no payload model)"""
        await message.reply("status.response", {"status": "healthy"})
```

**Decorator Parameters:**
- `message_type`: String identifier for the message type
- `model`: Optional Pydantic model for payload validation (if None, payload will be None)

**Handler Signature:**
- `message: AgentMessage` - The received message envelope
- `payload: BaseModel | None` - Validated payload (if model provided) or None

**Benefits:**
- Automatic payload validation
- Type safety with IDE support
- Clean separation of concerns

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
    message_type: str
    data: dict[str, Any]
    meta: MessageMeta
    timestamp: float
    message_id: str
```

**Properties:**
- `sender_id: str` - ID of sender agent
- `target_id: str` - ID of target agent
- `message_type: str` - Message type identifier
- `data: dict[str, Any]` - Message payload (business data)
- `meta: MessageMeta` - Transport metadata (correlation ID, reply flags, version)
- `timestamp: float` - Unix timestamp when message was created
- `message_id: str` - Unique message identifier
- `expects_reply: bool` - Whether message expects a reply (convenience property)
- `is_reply: bool` - Whether message is a reply to a request (convenience property)
- `payload: dict` - Alias for `data` (backward compatibility)

**Methods:**

#### `async reply(message_type: str, payload: dict) -> None`
Reply to this message (request-response pattern).

```python
@Agent.on("query.request", model=QueryRequest)
async def handle_query(self, message: AgentMessage, payload: QueryRequest):
    if message.expects_reply:
        result = await self.process(payload)
        await message.reply("query.response", {"result": result})
```

**Parameters:**
- `message_type`: Message type identifier for the reply
- `payload`: Response payload dictionary

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

### Chemistry Tutoring
`examples/chemistry_tutoring/`

Two agents (student and professor) having an educational conversation using OpenAI.

**Demonstrates:**
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

A: Currently only Redis is supported. The framework is designed around Redis primitives (streams, hashes).

### Reporting Issues

Found a bug or have a feature request? Open an issue on GitHub:

https://github.com/yourusername/mas-framework/issues

### Contributing

Contributions are welcome! See [AGENTS.md](AGENTS.md) for development workflow.

---

**License**: MIT License - see [LICENSE](LICENSE) file for details

**Version**: See [src/mas/__version__.py](src/mas/__version__.py) for current version
