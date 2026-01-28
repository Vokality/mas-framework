# Authorization

MAS is deny-by-default.

Authorization is configured in `mas.yaml` under `permissions:`. The runner applies these rules to Redis so the server can enforce them.

## Permission Specs

Bidirectional

```yaml
permissions:
  - type: allow_bidirectional
    agents: [agent_a, agent_b]
```

One-way allow

```yaml
permissions:
  - type: allow
    sender: agent_a
    targets: [agent_b, agent_c]
```

Network

```yaml
permissions:
  - type: allow_network
    agents: [agent_a, agent_b, agent_c]
    bidirectional: true
```

Broadcast

```yaml
permissions:
  - type: allow_broadcast
    sender: coordinator
    receivers: [worker_1, worker_2]
```

Wildcard (use sparingly)

```yaml
permissions:
  - type: allow_wildcard
    agent_id: admin
```

## Redis Data Model

Rules are stored as sets:
- `agent:{agent_id}:allowed_targets`
- `agent:{agent_id}:blocked_targets`
