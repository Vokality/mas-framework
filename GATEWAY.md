# Security Pipeline (Server)

The MAS server runs a centralized policy pipeline on every message:

1. AuthN (mTLS)
   - Identity comes from client certificate SPIFFE URI SAN
2. AuthZ
   - Deny-by-default
   - ACL allow/block lists
   - Optional RBAC
3. Rate limiting
4. Circuit breaker
5. DLP scanning
   - Block or redact based on policy
6. Audit log
   - Server writes decision + metadata to Redis Streams

Agents never connect to Redis.

## Redis Streams / Keys

- Delivery streams
  - `agent.stream:{agent_id}` (shared across instances)
  - `agent.stream:{agent_id}:{instance_id}` (replies pinned to a specific requester instance)
- Audit
  - `audit:messages` (server decisions and metadata)
- Dead letter queue
  - `dlq:messages` (delivery failures / rejects)
- Authorization
  - `agent:{agent_id}:allowed_targets` (set)
  - `agent:{agent_id}:blocked_targets` (set)
- State
  - `agent.state:{agent_id}` (hash)
