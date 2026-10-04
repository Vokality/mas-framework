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
- Serves an optional read-only management dashboard and health API.
- Maintains bounded process counters and emits OpenTelemetry traces and metrics when configured.
- Uses Redis Streams for durable, at-least-once delivery.
- Uses Redis hashes for agent state.

## Components

- `packages/mas-proto/`: protobuf contract and generated bindings.
- `packages/mas-core/`: shared message, JSON, Redis, and telemetry primitives.
- `packages/mas-gateway/`: policy modules used by the server.
- `packages/mas-server/`: gRPC+mTLS MAS server package.
- `packages/mas-agent/`: agent client runtime.

## Message Flow

Send:
1. Agent calls `send(target_id, message_type, data)`.
2. Server validates, audits, and routes by writing an envelope JSON into a Redis Stream.
3. Server session tasks for the target agent consume from Redis Streams (`agent.stream:{agent_id}`) and deliver over the gRPC `Transport` stream.
4. Agent ACKs or NACKs deliveries; the server atomically acknowledges and deletes owned entries, atomically requeues retryable failures with an attempt count, or durably preserves failures in the DLQ before acknowledging. Handler attempts are bounded by `max_delivery_attempts` (default 5).

Delivery is at-least-once. A handler can see the same `message_id` again after
disconnects, slow ACKs, or stream reclaim, so handlers that cause side effects
must be idempotent or deduplicate by `message_id`.

Request/reply:
1. Request creates a correlation id; server stores request origin in `mas.pending_request:{correlation_id}` with a TTL matching the request timeout (60 seconds by default when the client omits an explicit timeout).
2. Policy rejection removes the pending key. An uncertain storage confirmation retains it because the request may already be queued.
3. Responder replies with the `correlation_id`; the server verifies the reply sender and policy, then atomically enqueues the reply, writes a receipt and consumes the unchanged pending key. Identical retries return the original message ID until the original request deadline; changed reply content is rejected.
4. The requesting client binds each pending request to its target agent and accepts a reply only from that agent, so a reply cannot be resolved by an unrelated agent that knows the correlation id.

Multi-instance:
- Shared delivery stream per agent id distributes work across instances through Redis consumer groups.
- Reply stream per agent and instance ensures replies go back to the requesting process.
- A shared Redis lease owns each agent/instance across all brokers. Exact owner tokens fence renewal, ACK, retry and delivery. Expired ownership cannot release a successor's lease or acknowledge its work.
- Lease TTL is six seconds with renewal every two seconds; disconnected work remains durable for consumer-group recovery.

## State

- State lives in Redis under `agent.state:{agent_id}`.
- Agents access state only via gRPC (`GetState`, `UpdateState`, `ResetState`).
- Reads return a revision with the fields. Updates and resets must supply that revision; stale writes return `ABORTED` instead of overwriting concurrent changes. Clients refresh state before retrying business logic.
- Storage acknowledgement policy is explicit: optional same-connection `WAIT` or `WAITAOF` confirmation. A failed barrier means the commit is uncertain; it does not imply rollback.

## Security Model

- mTLS is mandatory.
- Agent identity comes from the certificate SAN; callers cannot spoof `sender_id`.
- Authorization is deny-by-default.
- Audit logs are written server-side for policy decisions and are hash-chained for tamper detection.
