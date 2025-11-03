# MAS Framework Architecture

## Table of Contents
- [Overview](#overview)
- [Design Philosophy](#design-philosophy)
- [Core Components](#core-components)
- [Message Flow](#message-flow)
- [State Management](#state-management)
- [Discovery System](#discovery-system)
- [Health Monitoring](#health-monitoring)
- [Redis Data Model](#redis-data-model)
- [Lifecycle Management](#lifecycle-management)
- [Performance Characteristics](#performance-characteristics)
- [Gateway Pattern](#gateway-pattern-enterprise-deployment)
- [Configuration](#configuration)
- [Implementation Details](#implementation-details)

## Overview

MAS Framework is a lightweight, peer-to-peer multi-agent system built on Redis. Agents communicate directly with each other using Redis pub/sub channels, without routing through a central server.

### Architecture: Peer-to-Peer Messaging

```
Agent A → Redis Pub/Sub (channel: agent.B) → Agent B
              (direct, no routing)
```

This architectural approach provides:
- **High throughput**: 10,000+ messages/second
- **Low latency**: <5ms median latency
- **No single point of failure**: Agents communicate directly
- **Linear scalability**: Adding agents doesn't create bottlenecks

## Design Philosophy

The framework is built on five core principles:

### 1. Peer-to-Peer Communication
Agents publish messages directly to target agent channels. The MAS Service never touches message content.

### 2. Minimal Central Service
The MAS Service only handles:
- Agent registration/deregistration
- Discovery queries
- Health monitoring

It does NOT:
- Route messages
- Store message content
- Act as a message broker

### 3. Auto-Persisted State
Agent state automatically persists to Redis, surviving restarts without manual save/load logic.

### 4. Minimal Bootstrapping
3 lines of code to create and run an agent:
```python
agent = Agent("my_agent", capabilities=["chat"])
await agent.start()
await agent.send("other_agent", {"hello": "world"})
```

### 5. Redis as Single Dependency
All functionality (messaging, state, discovery, health) uses Redis primitives: pub/sub, hashes, strings with TTL.

## Core Components

### 1. Agent (`src/mas/agent.py`)

**Purpose**: Self-contained agent that communicates peer-to-peer.

**Key Responsibilities**:
- Subscribe to own Redis channel: `agent.{agent_id}`
- Send messages to other agents' channels: `agent.{target_id}`
- Manage state persistence
- Handle lifecycle (start/stop)
- Send heartbeats
- Provide discovery interface

**Internal Architecture**:
```python
Agent
├── _redis: Redis           # Redis connection
├── _pubsub: PubSub        # Message subscription
├── _registry: Registry     # Registration handler
├── _state_manager: State   # State persistence
├── _tasks: [Task]         # Background tasks
│   ├── _message_loop()    # Listen for incoming messages
│   └── _heartbeat_loop()  # Send periodic heartbeats
└── _running: bool         # Lifecycle flag
```

**Lifecycle Flow**:
```
start()
  ├─→ Connect to Redis
  ├─→ Register with registry
  ├─→ Load state from Redis
  ├─→ Subscribe to agent.{id} channel
  ├─→ Start background tasks
  │   ├─→ _message_loop (listen for messages)
  │   └─→ _heartbeat_loop (every 30s)
  ├─→ Publish REGISTER to mas.system
  └─→ Call on_start() hook

stop()
  ├─→ Call on_stop() hook
  ├─→ Publish DEREGISTER to mas.system
  ├─→ Cancel background tasks
  ├─→ Deregister from registry
  ├─→ Unsubscribe from pub/sub
  └─→ Close Redis connection
```

**Message Loop** (`_message_loop`):
```python
async def _message_loop(self):
    async for message in self._pubsub.listen():
        if message["type"] == "message":
            msg = AgentMessage.parse(message["data"])
            await self.on_message(msg)  # User hook
```

**Heartbeat Loop** (`_heartbeat_loop`):
```python
async def _heartbeat_loop(self):
    while self._running:
        await self._registry.update_heartbeat(self.id)
        await asyncio.sleep(30)
```

### 2. MAS Service (`src/mas/service.py`)

**Purpose**: Lightweight registry and health monitor (NOT a message router).

**Key Responsibilities**:
- Listen for agent registration/deregistration
- Monitor agent heartbeats
- Mark inactive agents
- Log system events

**Background Tasks**:
1. **System Message Handler** (`_handle_system_messages`):
   - Subscribes to `mas.system` channel
   - Processes REGISTER/DEREGISTER events
   - Logs registration activity

2. **Health Monitor** (`_monitor_health`):
   - Scans for `agent:*:heartbeat` keys every 30s
   - Checks TTL on heartbeat keys
   - Marks agents as INACTIVE if heartbeat expired

**Important**: The service does NOT route messages. Agents communicate directly.

### 3. Agent Registry (`src/mas/registry.py`)

**Purpose**: Manage agent registration and discovery in Redis.

**Core Operations**:

**Register** (`register`):
```python
agent_data = {
    "id": agent_id,
    "capabilities": json.dumps(capabilities),
    "metadata": json.dumps(metadata),
    "status": "ACTIVE",
    "token": generate_token(),
    "registered_at": str(time.time()),
}
await redis.hset(f"agent:{agent_id}", mapping=agent_data)
```

**Deregister** (`deregister`):
```python
await redis.delete(f"agent:{agent_id}")
await redis.delete(f"agent:{agent_id}:heartbeat")
# State preserved by default (configurable)
```

**Discover** (`discover`):
```python
# Scan all agent:* keys
async for key in redis.scan_iter("agent:*"):
    agent_data = await redis.hgetall(key)
    if agent_data["status"] == "ACTIVE":
        if capabilities_match(agent_data, required):
            yield agent_data
```

**Heartbeat** (`update_heartbeat`):
```python
# String with 60s TTL
await redis.setex(
    f"agent:{agent_id}:heartbeat",
    60,
    str(time.time())
)
```

### 4. State Manager (`src/mas/state.py`)

**Purpose**: Automatic state persistence to Redis.

**State Models**:
- **Dict mode**: Plain dictionary, flexible
- **Pydantic mode**: Type-safe with validation

**Operations**:

**Load** (`load`):
```python
data = await redis.hgetall(f"agent.state:{agent_id}")
if state_model == dict:
    self._state = data
else:
    self._state = state_model(**data)  # Pydantic
```

**Update** (`update`):
```python
# Update in-memory
if isinstance(state, BaseModel):
    for key, val in updates.items():
        setattr(state, key, val)
else:
    state.update(updates)

# Persist to Redis
redis_data = {k: json.dumps(v) if complex else str(v)
              for k, v in state.items()}
await redis.hset(f"agent.state:{agent_id}", mapping=redis_data)
```

**Reset** (`reset`):
```python
self._state = state_model() if state_model else {}
await redis.delete(f"agent.state:{agent_id}")
```

### 5. Protocol (`src/mas/protocol.py`)

**Purpose**: Define message formats.

**AgentMessage**:
```python
class AgentMessage(BaseModel):
    sender_id: str
    target_id: str
    payload: dict
    timestamp: float = time.time()
    message_id: str = str(time.time_ns())
```

**Message**: Generic envelope (less commonly used):
```python
class Message(BaseModel):
    sender_id: str
    target_id: str
    message_type: MessageType
    payload: dict
```

## Message Flow

### Peer-to-Peer Send

```
1. Agent A calls send("agent_b", payload)
   ↓
2. Create AgentMessage
   sender_id: "agent_a"
   target_id: "agent_b"
   payload: {...}
   ↓
3. Publish to Redis channel: agent.agent_b
   await redis.publish("agent.agent_b", message.json())
   ↓
4. Agent B's pubsub listener receives
   ↓
5. Agent B's _message_loop parses message
   ↓
6. Agent B's on_message(message) called
```

### System Messages (Registration)

```
1. Agent starts and calls agent.start()
   ↓
2. Agent publishes to mas.system:
   {
     "type": "REGISTER",
     "agent_id": "agent_a",
     "capabilities": ["nlp", "text"]
   }
   ↓
3. MAS Service receives on mas.system channel
   ↓
4. MAS Service logs registration
   (No further action - agent already registered in Redis)
```

### Discovery Flow

```
1. Agent A calls discover(capabilities=["nlp"])
   ↓
2. Registry scans Redis for agent:* keys
   ↓
3. For each key:
   - Read capabilities
   - Check if status == "ACTIVE"
   - Check if capabilities match
   ↓
4. Return list of matching agents
   [
     {"id": "agent_b", "capabilities": ["nlp", "text"]},
     {"id": "agent_c", "capabilities": ["nlp", "voice"]}
   ]
   ↓
5. Agent A can now send() to discovered agents
```

## State Management

### State Lifecycle

```
Agent Creation
  ↓
start() → StateManager.load()
  ↓
Load from Redis: agent.state:{agent_id}
  ↓
If not exists: Initialize defaults
  ↓
In-Memory State (dict or Pydantic model)
  ↓
update_state(changes)
  ↓
Auto-persist to Redis
  ↓
stop() → State remains in Redis
  ↓
Restart agent with same ID
  ↓
State automatically restored
```

### Type Safety with Pydantic

```python
class MyState(BaseModel):
    counter: int = 0
    name: str = ""
    active: bool = True

agent = Agent("my_agent", state_model=MyState)
await agent.start()

# Type-safe access
await agent.update_state({"counter": 42})
print(agent.state.counter)  # int: 42

# Validation
await agent.update_state({"counter": "invalid"})  # ValidationError
```

### Redis Storage Format

State is stored as a Redis hash:
```
HSET agent.state:my_agent
  counter "42"
  name "test"
  active "true"
  metadata "{\"key\": \"value\"}"  # JSON for complex types
```

## Discovery System

### Capability-Based Discovery

Agents register with capability tags:
```python
agent = Agent("nlp_agent", capabilities=["nlp", "text", "translation"])
await agent.start()
```

Other agents discover by capability:
```python
nlp_agents = await agent.discover(capabilities=["nlp"])
# Returns all agents with "nlp" capability
```

### Discovery Implementation

**Registry Side**:
```python
async def discover(self, capabilities):
    agents = []
    async for key in redis.scan_iter("agent:*"):
        if key.count(":") != 1:  # Skip agent:id:heartbeat
            continue
        
        agent_data = await redis.hgetall(key)
        if agent_data["status"] != "ACTIVE":
            continue
        
        agent_caps = json.loads(agent_data["capabilities"])
        if not capabilities or any(c in agent_caps for c in capabilities):
            agents.append(agent_data)
    
    return agents
```

**Performance**: O(N) scan of all agents, filtered by capability. Redis SCAN is non-blocking.

## Health Monitoring

### Heartbeat System

**Agent Side** (every 30 seconds):
```python
async def _heartbeat_loop(self):
    while self._running:
        await self._registry.update_heartbeat(self.id)
        await asyncio.sleep(30)
```

**Registry Side** (update heartbeat):
```python
await redis.setex(
    f"agent:{agent_id}:heartbeat",
    60,  # TTL: 60 seconds
    str(time.time())
)
```

**MAS Service Side** (monitor):
```python
async def _monitor_health(self):
    while self._running:
        async for key in redis.scan_iter("agent:*:heartbeat"):
            ttl = await redis.ttl(key)
            if ttl < 0:  # Expired
                agent_id = extract_id(key)
                await redis.hset(f"agent:{agent_id}", "status", "INACTIVE")
        await asyncio.sleep(30)
```

### Heartbeat Timeline

```
t=0s:  Agent sends heartbeat → TTL set to 60s
t=30s: Agent sends heartbeat → TTL reset to 60s
t=60s: Agent sends heartbeat → TTL reset to 60s
...
(Agent crashes)
t=90s: Last heartbeat expires (60s TTL elapsed)
t=120s: MAS Service scan detects expired heartbeat
       → Marks agent as INACTIVE
```

## Redis Data Model

### Key Schema

| Key Pattern | Type | Purpose | TTL |
|------------|------|---------|-----|
| `agent:{id}` | Hash | Agent metadata | None |
| `agent:{id}:heartbeat` | String | Last heartbeat timestamp | 60s |
| `agent.state:{id}` | Hash | Agent state | None |
| `agent.{id}` | Pub/Sub Channel | Agent message channel | N/A |
| `mas.system` | Pub/Sub Channel | System messages | N/A |

### Data Structures

**Agent Metadata** (`agent:{id}`):
```
HGETALL agent:my_agent
{
  "id": "my_agent",
  "capabilities": "[\"nlp\", \"text\"]",
  "metadata": "{\"version\": \"1.0\"}",
  "status": "ACTIVE",
  "token": "abc123...",
  "registered_at": "1699999999.123"
}
```

**Agent Heartbeat** (`agent:{id}:heartbeat`):
```
GET agent:my_agent:heartbeat
"1699999999.456"
TTL: 45 seconds remaining
```

**Agent State** (`agent.state:{id}`):
```
HGETALL agent.state:my_agent
{
  "counter": "42",
  "name": "test",
  "status": "active",
  "data": "{\"nested\": \"json\"}"
}
```

### Pub/Sub Channels

**Agent Channel** (`agent.{id}`):
- Each agent subscribes to its own channel
- Other agents publish messages here
- Format: JSON-encoded AgentMessage

**System Channel** (`mas.system`):
- MAS Service subscribes
- Agents publish REGISTER/DEREGISTER events
- Format: JSON with type + data

## Lifecycle Management

### Agent Lifecycle

```
┌─────────────┐
│  Created    │
└──────┬──────┘
       │ start()
       ↓
┌─────────────┐
│ Registering │
│ - Connect Redis
│ - Register in registry
│ - Load state
│ - Subscribe to channel
└──────┬──────┘
       │
       ↓
┌─────────────┐
│   Active    │◄──┐
│ - Send/receive  │
│ - Heartbeat     │ (running)
│ - State updates │
└──────┬──────┘   │
       │          │
       ↓          │
       └──────────┘
       │ stop()
       ↓
┌─────────────┐
│  Stopping   │
│ - Deregister
│ - Cancel tasks
│ - Close connections
└──────┬──────┘
       │
       ↓
┌─────────────┐
│  Stopped    │
└─────────────┘
```

### Graceful Shutdown

```python
async def stop(self):
    # 1. Set flag to stop loops
    self._running = False
    
    # 2. Call user cleanup
    await self.on_stop()
    
    # 3. Publish deregistration
    await redis.publish("mas.system", {
        "type": "DEREGISTER",
        "agent_id": self.id
    })
    
    # 4. Cancel background tasks
    for task in self._tasks:
        task.cancel()
    await asyncio.gather(*self._tasks, return_exceptions=True)
    
    # 5. Cleanup Redis
    await self._registry.deregister(self.id)
    await self._pubsub.unsubscribe()
    await self._pubsub.aclose()
    await self._redis.aclose()
```

### State Persistence on Restart

```python
# First run
agent = Agent("my_agent")
await agent.start()
await agent.update_state({"counter": 42})
await agent.stop()

# State saved to Redis: agent.state:my_agent

# Second run (same agent_id)
agent2 = Agent("my_agent")  # Same ID!
await agent2.start()
# StateManager.load() automatically restores state
print(agent2.state["counter"])  # "42"
```

## Performance Characteristics

### Throughput

| Operation | Performance | Notes |
|-----------|-------------|-------|
| Message send | 10,000+ msg/sec | Redis pub/sub |
| State update | 5,000+ ops/sec | Redis hash set |
| Discovery | 1,000+ queries/sec | Redis scan |
| Heartbeat | 30s interval | Minimal overhead |

### Latency

| Metric | Value |
|--------|-------|
| P50 (median) | <5ms |
| P95 | <10ms |
| P99 | <50ms |

### Scalability

- **Agents**: Tested with 100+ agents, Redis supports 10,000+ pub/sub channels
- **Messages**: Linear scaling, no central bottleneck
- **Discovery**: O(N) scan, acceptable for <1000 agents
- **State**: O(1) per agent, hash operations

### Bottlenecks

1. **Redis itself**: Single Redis instance limits
   - Solution: Redis Cluster for horizontal scaling
   
2. **Discovery scans**: O(N) for large agent counts
   - Solution: Add capability index using Redis sets
   
3. **Pub/Sub reliability**: At-most-once delivery
   - Solution: Redis Streams for at-least-once (roadmap)

## Implementation Details

### Async/Await Pattern

All I/O operations are async:
```python
# Agent start
await agent.start()

# Message send
await agent.send(target_id, payload)

# State update
await agent.update_state(changes)

# Discovery
agents = await agent.discover(capabilities)
```

Background tasks use `asyncio.create_task()`:
```python
self._tasks = [
    asyncio.create_task(self._message_loop()),
    asyncio.create_task(self._heartbeat_loop()),
]
```

### Error Handling

**Message Loop**:
```python
async for message in self._pubsub.listen():
    try:
        msg = AgentMessage.parse(message["data"])
        await self.on_message(msg)
    except Exception as e:
        logger.error("Failed to handle message", exc_info=e)
        # Continue processing other messages
```

**Heartbeat Loop**:
```python
try:
    while self._running:
        await self._registry.update_heartbeat(self.id)
        await asyncio.sleep(30)
except asyncio.CancelledError:
    pass  # Graceful shutdown
except Exception as e:
    logger.error("Heartbeat failed", exc_info=e)
```

### Testing Strategy

**Test Fixtures**:
```python
@pytest.fixture(autouse=True)
async def cleanup_redis():
    # Clean all agent:* and agent.state:* keys before each test
    redis = Redis.from_url("redis://localhost")
    async for key in redis.scan_iter("agent*"):
        await redis.delete(key)
    yield
```

**Test Agent Pattern**:
```python
class ReceiverAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []
        self.message_event = asyncio.Event()
    
    async def on_message(self, message):
        self.messages.append(message)
        self.message_event.set()

# In test
agent = ReceiverAgent("test_agent")
await agent.start()
await other_agent.send("test_agent", {"data": "test"})
await asyncio.wait_for(agent.message_event.wait(), timeout=2.0)
assert len(agent.messages) == 1
```

### Logging

Structured logging with context:
```python
logger.info(
    "Agent started",
    extra={"agent_id": self.id}
)

logger.debug(
    "Message sent",
    extra={
        "from": self.id,
        "to": target_id,
        "message_id": message.message_id
    }
)

logger.error(
    "Failed to handle message",
    exc_info=e,
    extra={"agent_id": self.id}
)
```

### Type Safety

**Pydantic Models**:
- `AgentMessage`: Message validation
- `Message`: Generic message envelope
- `BaseModel`: State models (optional)

**Type Hints**:
```python
async def send(self, target_id: str, payload: dict) -> None:
async def discover(self, capabilities: list[str] | None = None) -> list[dict]:
async def update_state(self, updates: dict) -> None:
```

**Pyright Configuration**:
```toml
[tool.pyright]
typeCheckingMode = "standard"
reportMissingImports = "error"
enableReachabilityAnalysis = true
```

## Usage Pattern

### Basic Agent Creation

```python
from mas import Agent

agent = Agent("my_agent", capabilities=["nlp"])
await agent.start()
await agent.send("target", payload)
```

### Agent Characteristics

- **Self-contained**: Only needs agent ID and Redis URL
- **Optional MAS Service**: Service is for monitoring, not required for messaging
- **Simple lifecycle**: Single `await agent.start()` call
- **Direct messaging**: Peer-to-peer communication
- **Auto-persisted state**: Automatic state management
- **Minimal code**: ~200 lines per component

## Gateway Pattern (Enterprise Deployment)

The framework includes an optional **Gateway Service** for enterprise deployments requiring centralized security, compliance, and operational control.

### Gateway Architecture

```
Agent A → Gateway Service → Validation → Redis Streams → Agent B
          (auth, audit, DLP)            (reliable delivery)
```

**Gateway Features**:
- **Authentication**: Token-based validation with revocation support
- **Authorization**: ACL (allow/block lists) and RBAC (role-based permissions)
- **Rate Limiting**: Token bucket algorithm (per-minute, per-hour limits)
- **Data Loss Prevention**: PII/PHI/PCI pattern detection with block/redact/alert actions
- **Circuit Breakers**: Failure detection with auto-recovery and dead letter queues
- **Priority Queues**: Message prioritization (CRITICAL > HIGH > NORMAL > LOW > BULK)
- **Audit Logging**: Immutable audit trail with hash chains in Redis Streams
- **Message Signing**: HMAC-SHA256 signatures with replay attack protection
- **Metrics**: Prometheus instrumentation for observability

### Gateway Configuration

The gateway uses Pydantic Settings with multiple configuration sources:

```python
from mas.gateway import GatewaySettings, load_settings

# From environment variables
settings = GatewaySettings()

# From YAML file
settings = GatewaySettings.from_yaml("gateway.yaml")

# Programmatic
settings = GatewaySettings(
    redis={"url": "redis://prod:6379"},
    features={"rbac": True, "message_signing": True}
)
```

**Configuration Hierarchy** (highest to lowest priority):
1. Explicitly passed parameters
2. Environment variables (GATEWAY_* prefix)
3. Config file (YAML)
4. Production-ready defaults

**Default Features** (secure-by-default):
- DLP scanning: **Enabled**
- Circuit breakers: **Enabled**
- Priority queues: **Enabled**
- RBAC authorization: **Enabled**
- Message signing: **Enabled**

### Gateway Components

**Authentication Module** (`src/mas/gateway/authentication.py`):
- Token validation against Redis registry
- Token rotation and revocation
- Configurable token lifetime

**Authorization Module** (`src/mas/gateway/authorization.py`):
- ACL: Simple allow/block lists with wildcard support
- RBAC: Role-based permissions with pattern matching (e.g., `send:*`, `agent.*`)
- Combined authorization (either ACL or RBAC can grant access)

**Audit Module** (`src/mas/gateway/audit.py`):
- Immutable audit logging using Redis Streams
- Hash chains for tamper detection
- Query API: by sender, target, decision, violation type, time range
- CSV/JSON export for compliance reporting

**DLP Module** (`src/mas/gateway/dlp.py`):
- Pattern-based detection: SSN, credit cards, AWS keys, phone numbers, emails
- Actions: BLOCK (reject), REDACT (mask), ALERT (log)
- Metrics integration for violation tracking

**Rate Limit Module** (`src/mas/gateway/rate_limit.py`):
- Token bucket algorithm
- Per-agent limits (configurable per-minute and per-hour)
- Sliding window implementation using Redis sorted sets

**Circuit Breaker Module** (`src/mas/gateway/circuit_breaker.py`):
- States: CLOSED (normal), OPEN (blocking), HALF_OPEN (testing recovery)
- Dead letter queue for failed messages
- Configurable thresholds and timeouts

**Priority Queue Module** (`src/mas/gateway/priority_queue.py`):
- Five priority levels with configurable weights
- Weighted round-robin scheduling
- Message TTL and fairness guarantees

**Message Signing Module** (`src/mas/gateway/message_signing.py`):
- HMAC-SHA256 cryptographic signatures
- Per-agent signing keys with rotation support
- Replay protection: nonce tracking and timestamp validation
- Timing-safe signature comparison

**Metrics Module** (`src/mas/gateway/metrics.py`):
- Prometheus counters: messages, auth failures, rate limits, DLP violations
- Histograms: message latency, DLP scan duration, auth duration
- Gauges: active requests, circuit breaker states, queue depth
- HTTP server for /metrics endpoint

### Gateway Redis Data Model

Additional keys used by gateway:

| Key Pattern | Type | Purpose |
|------------|------|---------|
| `agent:{id}:allowed_targets` | Set | ACL allowed targets |
| `agent:{id}:blocked_targets` | Set | ACL blocked targets |
| `agent:{id}:roles` | Set | RBAC role assignments |
| `agent:{id}:signing_key` | String | HMAC signing key |
| `role:{name}` | Hash | Role metadata |
| `role:{name}:permissions` | Set | Role permissions |
| `ratelimit:{id}:{window}` | Sorted Set | Rate limit tracking |
| `circuit:{target}` | Hash | Circuit breaker state |
| `dlq:messages` | Stream | Dead letter queue |
| `audit:messages` | Stream | Main audit log |
| `audit:by_sender:{id}` | Stream | Sender-indexed audit |
| `audit:by_target:{id}` | Stream | Target-indexed audit |
| `audit:security_events` | Stream | Security events |
| `message_nonces:{id}` | String | Nonce replay protection |
| `priority_queue:{target}:{priority}` | Sorted Set | Priority queues |

### When to Use Gateway vs Pure P2P

**Use Gateway Pattern if**:
- Regulated industry (finance, healthcare, government)
- Handling sensitive data (PII, PHI, PCI)
- Compliance requirements (SOC2, HIPAA, GDPR)
- Multi-tenant with strict isolation
- Zero-trust security architecture required

**Use Pure P2P if**:
- Internal tools in trusted environment
- Performance is critical (<5ms latency required)
- No regulatory compliance requirements
- Rapid iteration and development

**Hybrid Approach**:
- P2P for internal, trusted agents (high performance)
- Gateway for external agents (security)
- Gateway for sensitive operations (compliance)

### Gateway Performance

**Throughput** (single gateway instance):
- Basic (auth + audit): 5,000-8,000 msg/s
- Full security (all features): 2,000-3,000 msg/s
- Clustered (3 instances): 6,000-9,000 msg/s

**Latency**:
- P50: 10-15ms (vs <5ms for pure P2P)
- P95: 25-35ms
- P99: 50-80ms

**Trade-offs**:
- 2-3x latency increase
- 50-70% throughput reduction (single instance)
- Complete audit trail and compliance
- Zero-trust security enforcement

## Configuration

The framework provides comprehensive configuration management using Pydantic Settings.

### Configuration Sources

Configuration loads from multiple sources with clear precedence:

1. **Explicitly passed parameters** (highest priority)
2. **Environment variables** (`GATEWAY_*` prefix)
3. **YAML configuration file** (`gateway.yaml`)
4. **Default values** (production-ready defaults)

### Gateway Configuration

```python
from mas.gateway import GatewaySettings, load_settings

# Load from environment variables
settings = GatewaySettings()

# Load from YAML file
settings = GatewaySettings.from_yaml("gateway.yaml")

# Programmatic configuration
settings = GatewaySettings(
    redis={"url": "redis://prod:6379"},
    rate_limit={"per_minute": 200, "per_hour": 2000},
    features={"rbac": True, "message_signing": True}
)

# Using convenience function
settings = load_settings(config_file="gateway.yaml")

# View configuration
print(settings.summary())
```

### Configuration Modules

**GatewaySettings**: Main configuration container
- `redis`: Redis connection settings
- `rate_limit`: Rate limiting thresholds
- `features`: Feature flag toggles
- `circuit_breaker`: Circuit breaker parameters
- `priority_queue`: Queue weights and TTL
- `message_signing`: Signing and replay protection settings

**RedisSettings**:
```python
url: str = "redis://localhost:6379"
decode_responses: bool = True
socket_timeout: Optional[float] = None
```

**RateLimitSettings**:
```python
per_minute: int = 100  # Messages per minute per agent
per_hour: int = 1000   # Messages per hour per agent
```

**FeaturesSettings** (secure-by-default):
```python
dlp: bool = True               # Data Loss Prevention scanning
priority_queue: bool = True    # Message priority routing
rbac: bool = True             # Role-Based Access Control
message_signing: bool = True  # HMAC message signatures
circuit_breaker: bool = True  # Circuit breaker for reliability
```

**CircuitBreakerSettings**:
```python
failure_threshold: int = 5     # Failures before opening circuit
success_threshold: int = 2     # Successes before closing
timeout_seconds: float = 60.0  # Timeout before half-open state
window_seconds: float = 300.0  # Failure counting window
```

**PriorityQueueSettings**:
```python
default_ttl: int = 300         # Message TTL in seconds
critical_weight: int = 10      # CRITICAL priority weight
high_weight: int = 5           # HIGH priority weight
normal_weight: int = 2         # NORMAL priority weight
low_weight: int = 1            # LOW priority weight
bulk_weight: int = 0           # BULK priority weight
```

**MessageSigningSettings**:
```python
max_timestamp_drift: int = 300 # Max clock drift (5 minutes)
nonce_ttl: int = 300          # Nonce TTL for replay protection
```

### Environment Variables

All settings support environment variable overrides:

```bash
# Redis configuration
export GATEWAY_REDIS__URL="redis://prod:6379"
export GATEWAY_REDIS__SOCKET_TIMEOUT=30

# Rate limiting
export GATEWAY_RATE_LIMIT__PER_MINUTE=200
export GATEWAY_RATE_LIMIT__PER_HOUR=2000

# Feature flags
export GATEWAY_FEATURES__DLP=true
export GATEWAY_FEATURES__RBAC=true
export GATEWAY_FEATURES__MESSAGE_SIGNING=true

# Circuit breaker
export GATEWAY_CIRCUIT_BREAKER__FAILURE_THRESHOLD=10
export GATEWAY_CIRCUIT_BREAKER__TIMEOUT_SECONDS=120

# Priority queue
export GATEWAY_PRIORITY_QUEUE__CRITICAL_WEIGHT=20

# Message signing
export GATEWAY_MESSAGE_SIGNING__MAX_TIMESTAMP_DRIFT=600
```

### YAML Configuration

Example `gateway.yaml`:

```yaml
redis:
  url: redis://localhost:6379
  decode_responses: true

rate_limit:
  per_minute: 100
  per_hour: 1000

features:
  dlp: true
  priority_queue: true
  rbac: true
  message_signing: true
  circuit_breaker: true

circuit_breaker:
  failure_threshold: 5
  success_threshold: 2
  timeout_seconds: 60
  window_seconds: 300

priority_queue:
  default_ttl: 300
  critical_weight: 10
  high_weight: 5
  normal_weight: 2
  low_weight: 1
  bulk_weight: 0

message_signing:
  max_timestamp_drift: 300
  nonce_ttl: 300
```

### Configuration Methods

**Load from file**:
```python
settings = GatewaySettings.from_yaml("gateway.yaml")
```

**Export to file**:
```python
settings.to_yaml("gateway-export.yaml")
```

**Human-readable summary**:
```python
print(settings.summary())
# Output:
# Gateway Configuration:
#   Redis: redis://localhost:6379
#   Rate Limits: 100/min, 1000/hour
# 
# Features:
#   DLP: ✓
#   Priority Queue: ✓
#   RBAC: ✓
#   ...
```

### Development vs Production

**Development** (disable features for speed):
```python
settings = GatewaySettings(
    features={"rbac": False, "message_signing": False, "dlp": False}
)
```

**Production** (use defaults or explicit config):
```python
# All security features enabled by default
settings = GatewaySettings.from_yaml("production.yaml")
```

---

## Summary

MAS Framework achieves high performance through:
1. **Peer-to-peer messaging**: Agents publish directly to target channels
2. **Minimal central service**: Registry only, no message routing
3. **Redis primitives**: Pub/sub, hashes, strings with TTL
4. **Auto-persisted state**: State survives restarts automatically
5. **Simple API**: 3 lines to create and run an agent

This architecture delivers high throughput (10,000+ msg/sec) and low latency (<5ms) while maintaining simplicity and ease of use.
