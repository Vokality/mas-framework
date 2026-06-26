# Architecture

MAS Framework is a secure, centralized multi-agent runtime.

Agents are untrusted clients:
- They never connect to Redis.
- They communicate only with the MAS server over gRPC + mTLS.

The MAS server is the policy and routing boundary:
- Authenticates agents via client certificate SPIFFE URI SAN (`spiffe://mas/agent/{agent_id}`).
- Enforces deny-by-default authorization.
- Applies security policies through `mas-gateway`.
- Writes audit records.
- Emits OpenTelemetry traces and metrics when configured.
- Uses Redis Streams for durable, at-least-once delivery.
- Uses Redis hashes for agent state.

## Components

- `packages/mas-proto/`: protobuf contract and generated bindings.
- `packages/mas-core/`: shared message, JSON, Redis, logging, and telemetry primitives.
- `packages/mas-gateway/`: policy modules used by the server.
- `packages/mas-server/`: gRPC+mTLS MAS server package.
- `packages/mas-agent/`: agent client runtime.

## Message Flow

Send:
1. Agent calls `send(target_id, message_type, data)`.
2. Server validates, audits, and routes by writing an envelope JSON into a Redis Stream.
3. Server session tasks for the target agent consume from Redis Streams (`agent.stream:{agent_id}`) and deliver over the gRPC `Transport` stream.
4. Agent ACKs or NACKs deliveries; server XACKs, retries, or DLQs.

Delivery is at-least-once. A handler can see the same `message_id` again after
disconnects, slow ACKs, or stream reclaim, so handlers that cause side effects
must be idempotent or deduplicate by `message_id`.

Request/reply:
1. Request creates a correlation id; server stores request origin in Redis with TTL.
2. Responder replies with the `correlation_id`; server routes the reply to the origin instance stream.
3. The requesting client binds each pending request to its target agent and accepts a reply only from that agent, so a reply cannot be resolved by an unrelated agent that knows the correlation id.

Multi-instance:
- Shared delivery stream per agent id distributes work across instances through Redis consumer groups.
- Reply stream per agent and instance ensures replies go back to the requesting process.

## State

- State lives in Redis under `agent.state:{agent_id}`.
- Agents access state only via gRPC (`GetState`, `UpdateState`, `ResetState`).

## Security Model

- mTLS is mandatory.
- Agent identity comes from the certificate SAN; callers cannot spoof `sender_id`.
- Authorization is deny-by-default.
- Audit logs are written server-side for policy decisions.
