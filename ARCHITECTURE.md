# Architecture

MAS Framework is a secure, centralized multi-agent runtime.

Agents are untrusted clients:
- They never connect to Redis
- They communicate only with the MAS server over gRPC + mTLS

The MAS server is the policy and routing boundary:
- Authenticates agents via client certificate SPIFFE URI SAN (`spiffe://mas/agent/{agent_id}`)
- Enforces deny-by-default authorization
- Applies security policies (DLP, rate limits, circuit breaker)
- Writes a tamper-evident audit trail
- Uses Redis Streams for durable, at-least-once delivery
- Uses Redis hashes for agent state

## Components

- `src/mas/server.py`: gRPC+mTLS MAS server, owns all Redis responsibilities
- `src/mas/agent.py`: agent client, speaks only gRPC
- `src/mas/runner.py`: loads `mas.yaml`, starts the server and agent processes
- `src/mas/gateway/*`: policy modules used by the server (audit, authz, rate limit, DLP, circuit breaker)

## Message Flow

Send
1. Agent calls `send(target_id, message_type, data)`
2. Server validates + audits + routes by writing an envelope JSON into a Redis Stream
3. Server session(s) for the target agent consume from Redis Streams (`agent.stream:{agent_id}`) and deliver over the gRPC `Transport` stream
4. Agent ACKs/NACKs deliveries; server XACKs or DLQs

Request/Reply
1. Request creates a correlation id; server stores request origin in Redis with TTL
2. Responder replies with `correlation_id`; server routes the reply to the origin instance stream

Multi-instance
- Shared delivery stream per agent id distributes work across instances via Redis consumer groups
- Reply stream per agent+instance ensures replies go back to the requesting process

## State

- State lives in Redis under `agent.state:{agent_id}`
- Agents access state only via gRPC (`GetState`, `UpdateState`, `ResetState`)

## Security Model

- mTLS is mandatory; agents must present a client certificate
- Agent identity comes from the certificate SAN; callers cannot spoof `sender_id`
- Authorization is deny-by-default; configure explicit allow rules in `mas.yaml`
- Audit logs are written server-side for every decision
