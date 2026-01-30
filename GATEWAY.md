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

## Audit Decisions

Audit entries include a `decision` field that reflects the gateway outcome:

- `ALLOWED`: Message delivered with no blocking policy violations.
- `ALERT`: DLP found violations but allowed delivery.
- `DLP_REDACTED`: DLP redacted sensitive fields before delivery.
- `DLP_ENCRYPTED`: DLP encrypted sensitive fields before delivery.
- `DLP_BLOCKED`: DLP blocked delivery.
- `RATE_LIMITED`: Rate limit blocked delivery.
- `CIRCUIT_OPEN`: Circuit breaker blocked delivery.
- `AUTHZ_DENIED`: Authorization blocked delivery.

## Audit File Sink

You can optionally write audit entries to a local JSONL file with rotation using
`gateway.audit` in `mas.yaml`. When `file_path` is relative, it resolves from
the directory containing `mas.yaml`.

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
