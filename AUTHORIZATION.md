# Authorization Configuration Guide

## Overview

The MAS Framework provides a high-level `AuthorizationManager` API for configuring agent communication permissions. This abstraction provides excellent developer experience compared to manual Redis commands.

## Quick Start

```python
from mas.gateway import GatewayService

# Start gateway
gateway = GatewayService(redis_url="redis://localhost:6379")
await gateway.start()

# Get authorization manager
auth = gateway.auth_manager()

# Configure permissions
await auth.allow_bidirectional("agent1", "agent2")
```

## Common Patterns

### 1. Bidirectional Communication (Most Common)

Two agents that need to talk to each other:

```python
# Patient ↔ Doctor
await auth.allow_bidirectional("patient_jones", "doctor_smith")

# Student ↔ Professor
await auth.allow_bidirectional("student", "professor")
```

**Before (manual Redis):**
```python
redis = gateway._redis
await redis.sadd("agent:patient_jones:allowed_targets", "doctor_smith")
await redis.sadd("agent:doctor_smith:allowed_targets", "patient_jones")
```

**After (AuthorizationManager):**
```python
auth = gateway.auth_manager()
await auth.allow_bidirectional("patient_jones", "doctor_smith")
```

### 2. Full Mesh Network

All agents can message all other agents:

```python
# Team of agents
await auth.allow_network([
    "coordinator",
    "worker1",
    "worker2",
    "worker3"
])
```

This creates a full mesh where every agent can message every other agent.

### 3. Chain Network

Sequential communication (A → B → C):

```python
# Pipeline: agent1 → agent2 → agent3
await auth.allow_network(
    ["agent1", "agent2", "agent3"],
    bidirectional=False
)
```

### 4. Broadcast Pattern

One sender, multiple receivers (one-way):

```python
# Coordinator broadcasts to workers (workers can't reply)
await auth.allow_broadcast("coordinator", [
    "worker1",
    "worker2",
    "worker3"
])
```

### 5. Wildcard Permission

Agent can message anyone (use sparingly - admin/supervisor only):

```python
# Supervisor can message anyone
await auth.allow_wildcard("supervisor_agent")
```

### 6. Fluent API (Chainable)

Build complex configurations with method chaining:

```python
await (auth
    .allow("agent1", "agent2")
    .allow("agent2", ["agent3", "agent4"])
    .allow("agent3", "agent4")
    .apply())
```

### 7. Block-list (Advanced)

Allow broad access but block specific targets:

```python
await (auth
    .allow("agent1", "*")  # Can message anyone
    .block("agent1", "agent2")  # Except agent2
    .apply())
```

## Role-Based Access Control (RBAC)

For more complex scenarios, use role-based permissions:

```python
# Create roles
await (auth
    .create_role("doctor", 
        description="Medical professional role",
        permissions=["send:patient*"])
    .create_role("admin",
        description="Administrator role", 
        permissions=["send:*", "manage:*"])
    .apply())

# Assign roles to agents
await (auth
    .assign_role("dr_smith", "doctor")
    .assign_role("dr_jones", "doctor")
    .assign_role("super_admin", "admin")
    .apply())
```

### Permission Patterns

RBAC permissions support wildcard patterns:

- `"send:*"` - Can send to anyone
- `"send:patient*"` - Can send to any agent starting with "patient"
- `"manage:agent.*"` - Can manage agents matching pattern
- `"*"` - Full wildcard (all permissions)

## Debugging & Inspection

Get authorization summary for an agent:

```python
summary = await auth.get_summary("doctor_smith")
print(summary)
```

Output:
```python
{
    "agent_id": "doctor_smith",
    "acl": {
        "allowed": ["patient_jones"],
        "blocked": []
    },
    "roles": ["doctor"],
    "role_permissions": {
        "doctor": ["send:patient*"]
    }
}
```

## Examples

### Healthcare System

```python
# Configure bidirectional patient-doctor communication
auth = gateway.auth_manager()
await auth.allow_bidirectional("patient_jones", "doctor_smith")
```

See: `examples/healthcare_consultation/main.py`

### Multi-Agent Workflow

```python
# Coordinator orchestrates workers
auth = gateway.auth_manager()

# Coordinator can broadcast to all workers
await auth.allow_broadcast("coordinator", ["worker1", "worker2", "worker3"])

# Workers can only reply to coordinator
await auth.allow("worker1", "coordinator")
await auth.allow("worker2", "coordinator")
await auth.allow("worker3", "coordinator")

# Or use chainable API:
await (auth
    .allow("worker1", "coordinator")
    .allow("worker2", "coordinator")
    .allow("worker3", "coordinator")
    .apply())
```

### Enterprise Setup with Roles

```python
# Define organizational roles
await (auth
    .create_role("operator", permissions=["send:agent*"])
    .create_role("admin", permissions=["send:*", "manage:*"])
    .create_role("viewer", permissions=["read:*"])
    .apply())

# Assign roles
await (auth
    .assign_role("alice", "operator")
    .assign_role("bob", "admin")
    .assign_role("charlie", "viewer")
    .apply())
```

## Best Practices

1. **Use bidirectional for simple agent pairs** - Most common pattern
2. **Use networks for team collaboration** - Full mesh or chain
3. **Use broadcast for one-to-many** - Coordinator/worker patterns
4. **Reserve wildcards for admins** - Security principle of least privilege
5. **Use RBAC for enterprise** - Scales better than per-agent ACLs
6. **Configure before starting agents** - Avoid race conditions
7. **Use fluent API for complex setups** - More readable code

## Migration from Manual Redis

**Old way (painful):**
```python
redis = gateway._redis
await redis.sadd(f"agent:{sender}:allowed_targets", target)
await redis.sadd(f"agent:{target}:allowed_targets", sender)
```

**New way (delightful):**
```python
auth = gateway.auth_manager()
await auth.allow_bidirectional(sender, target)
```

## API Reference

### AuthorizationManager Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `allow_bidirectional(a, b)` | Enable two-way communication | `None` |
| `allow_network(agents, bidirectional=True)` | Full mesh or chain network | `None` |
| `allow_broadcast(sender, receivers)` | One-to-many communication | `None` |
| `allow_wildcard(agent_id)` | Grant wildcard permission | `None` |
| `allow(sender, targets)` | Queue permission (chainable) | `Self` |
| `block(sender, target)` | Queue block (chainable) | `Self` |
| `create_role(name, desc, perms)` | Queue role creation (chainable) | `Self` |
| `assign_role(agent_id, role)` | Queue role assignment (chainable) | `Self` |
| `apply()` | Apply queued operations | `None` |
| `get_summary(agent_id)` | Get authorization summary | `dict` |

### Usage Patterns

**Immediate execution:**
```python
await auth.allow_bidirectional("a", "b")  # Executes immediately
```

**Deferred execution (chainable):**
```python
await (auth
    .allow("a", "b")
    .allow("b", "c")
    .apply())  # Executes all at once
```

## See Also

- [Gateway Documentation](GATEWAY.md) - Full gateway feature set
- [Healthcare Example](examples/healthcare_consultation/) - Complete working example
- [Architecture Guide](ARCHITECTURE.md) - System design
