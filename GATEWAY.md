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
   - Block, redact, or alert based on policy
6. Audit log
   - Server writes decision + metadata to Redis Streams
   - Entries are hash-chained for tamper detection
7. Telemetry (optional)
   - OpenTelemetry traces and metrics for server, gateway, and agent runtime paths

Agents never connect to Redis.

## Audit Decisions

Audit entries include a `decision` field that reflects the gateway outcome:

- `ALLOWED`: Message delivered with no blocking policy violations.
- `ALERT`: DLP found violations but allowed delivery.
- `DLP_REDACTED`: DLP redacted sensitive fields before delivery.
- `DLP_BLOCKED`: DLP blocked delivery.
- `RATE_LIMITED`: Rate limit blocked delivery.
- `CIRCUIT_OPEN`: Circuit breaker blocked delivery.
- `AUTHZ_DENIED`: Authorization blocked delivery.

## Audit Hash Chain

Each audit entry includes a `payload_hash` and a `previous_hash` linking it to
the prior entry. The tail hash is stored in Redis at `audit:last_hash`. Writes
use a Redis transaction with optimistic locking so concurrent server processes
keep a single consistent chain. Use `AuditModule.verify_integrity(message_id)` to
validate the chain through that entry.

## Audit File Sink

You can optionally write audit entries to a local JSONL file with rotation using
`GatewaySettings.audit` or the `audit` section of a standalone gateway YAML file.

## OpenTelemetry

Configure telemetry with `GatewaySettings.telemetry` or the `telemetry` section of
a standalone gateway YAML file.

- `enabled`: turn OpenTelemetry on/off (default `false`)
- `otlp_endpoint`: OTLP/HTTP collector endpoint (for example `http://localhost:4318`)
- `service_name`, `service_namespace`, `environment`: resource attributes
- `sample_ratio`: trace sampling ratio (0.0-1.0)
- `export_metrics`: enable/disable OTLP metric export
- `metrics_export_interval_ms`: export interval for metrics
- `headers`: optional OTLP exporter headers

## Redis Streams / Keys

- Delivery streams
  - `agent.stream:{agent_id}` (shared across instances)
  - `agent.stream:{agent_id}:{instance_id}` (replies pinned to a specific requester instance)
- Request/reply
  - `mas.pending_request:{correlation_id}` (request origin + TTL; removed on reply or failed routing)
- Audit
  - `audit:messages` (all policy decisions and metadata)
  - `audit:by_sender:{sender_id}` (indexed by sender)
  - `audit:by_target:{target_id}` (indexed by target)
  - `audit:security_events` (security-specific events)
  - `audit:last_hash` (hash-chain tail)
- Dead letter queue
  - `dlq:messages` (delivery failures / rejects)
- Registry
  - `agent:{agent_id}` (hash: capabilities, metadata, status)
- Authorization
  - `agent:{agent_id}:allowed_targets` (set)
  - `agent:{agent_id}:blocked_targets` (set)
- State
  - `agent.state:{agent_id}` (hash)
