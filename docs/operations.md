# Operating MAS

Run at least two brokers against the same Redis primary namespace. Agents use
mTLS and retain their instance identity across reconnects. A broker owns an
instance through a Redis lease, so a second broker can validate unary operations
without pretending to own its transport. Lease expiry permits reconnect after
process death; exact owner tokens prevent stale ACKs and lease release.

## Broker process

Run `uv run python -m mas_server --settings broker.json --gateway gateway.yaml`.
The process handles SIGINT/SIGTERM and closes transports and listeners before
exiting, then flushes and shuts down telemetry. After listener startup it emits
one JSON line on stdout with `event: "mas.broker.ready"`, its PID, actual gRPC
`listen_addr`, and optional `management_url`. Supervisors can parse this line,
including when the configured gRPC port is zero. An embedding application can
construct the same settings directly.
Use a supervisor, restart policy, resource limits and graceful termination budget.

Minimal `broker.json`:

```json
{
  "listen_addr": "0.0.0.0:50051",
  "tls": {
    "server_cert_path": "/run/mas/tls/current/server.pem",
    "server_key_path": "/run/mas/tls/current/server.key",
    "client_ca_path": "/run/mas/tls/current/client-ca.pem",
    "revoked_certificates_path": "/run/mas/tls/current/revoked.json"
  },
  "agents": {
    "producer": {"agent_id": "producer", "capabilities": [], "metadata": {}},
    "worker": {"agent_id": "worker", "capabilities": ["work"], "metadata": {}}
  },
  "management": {
    "host": "0.0.0.0",
    "port": 8443,
    "auth_mode": "oidc",
    "oidc": {
      "issuer": "https://idp.example",
      "audience": "mas-management",
      "jwks_url": "https://idp.example/.well-known/jwks.json",
      "required_scopes": ["mas:read"],
      "allowed_roles": ["operator"]
    },
    "tls": {"cert_path": "/run/mas/https/server.pem", "key_path": "/run/mas/https/server.key"}
  }
}
```

Replace example provider endpoints and certificate paths before starting. The
operator must present a signed access JWT with the exact issuer and audience,
expiry, issued-at and subject, every configured scope, and at least one allowed
role when roles are configured. Allowed algorithms are pinned asymmetric
algorithms. JWKS fetching is asynchronous, bounded, cached and coalesced. Access
decisions record the verified subject without logging bearer credentials.
Issuer-side token lifetime and session revocation policies belong to the IdP;
MAS validates signed JWTs and does not implement token introspection.

The Svelte management dashboard provides dedicated Overview, Broker Fleet,
Performance, Message Traces, Alerts, Agents, Delivery Queues, Activity and
Telemetry pages. Direct page URLs and trace URLs support reload and browser
history. Search, state and capability filters, sorting, pagination, retained
window selection and JSON exports operate on the reported data. Shared themed
components provide paper and ink appearances. Refresh cadence, pause/resume and
manual refresh are available on every page; history and trace reads run only on
their relevant pages. Reader credentials stay in memory and are cleared by the
Access control or a reload. Management access remains read only.

The dashboard combines local sessions and workers with shared fleet health,
accepted traffic, delivery p95, retained performance charts, trace waterfalls,
active/resolved alerts and actual exporter outcomes. Python DTOs generate the
frontend types and CSP-safe boundary validators. The compiled dashboard is
included in the Python wheel and loaded once during async startup; a Node server
is not required in production. See [dashboard development](dashboard-development.md)
for source, build and contract checks. Every broker publishes
observations independently of HTTP and browser polling. Configure a unique stable
`broker_id` for each concurrently running broker; omission generates a UUID.
All fleet members must share the Redis namespace and observation settings.

`observations` defaults to enabled: heartbeats every second, stale after five
seconds, one hour of history (at most 3,600 points), at most 500 brokers,
120,000 pending spans, and a 60-second latency window. Set it to `null` to disable.
The default targets are 1,000 accepted messages/sec and delivery p95 below 300ms.
`trace_sample_every: 100` retains a deterministic 1% trace-detail sample plus
errors, at most 10,000 traces and 64 spans per trace. Capacity limits and missing
identities reduce coverage explicitly. Trace sampling applies to retained detail;
it does not reduce configured OTLP sampling or the correlated latency histogram.
Every client, RPC and ingress ancestor needed for a delivery join is retained;
optional stages of unsampled successful traces are skipped before model conversion
and persistence. Failed spans remain eligible for retained detail.

Live latency starts at the client send/request/reply API and ends at handler
entry or validated reply receipt. It excludes application scheduling before that
API, requires synchronized cross-host clocks, and uses conservative 1ms histogram
bins. Coverage accompanies the measurement; delayed, missing and dropped parent
spans remain partial. The acceptance harness separately includes scheduled
admission backlog. An idle or lightly offered workload does not breach the
capacity target. Throughput alerts require observed demand and delivery backlog.

Reader authentication protects `/api/snapshot`, `/api/history?limit=120`,
`/api/traces?limit=50`, and `/api/traces/{trace_id}`. Histories survive browser
reloads and aggregator changes within Redis retention. A trace outside retained
sampling or limits returns `404`; storage failures return sanitized `503`.
Exporter health records actual OTLP export acknowledgements, failures and last
success age. A successful acknowledgement does not prove downstream retention.

Optional `management.telemetry_ingest` enables binary OTLP at `/v1/traces`.
It requires a separate token or OIDC configuration with `mas:telemetry:write`;
reader credentials cannot write telemetry. Remote listeners still require OIDC
reader access and HTTPS. Explicit local ingestion is restricted to loopback.
The receiver retains static MAS operations and allowlisted scalar metadata,
excluding headers, payloads, events, exception text and arbitrary resource data.
It is trace-only: disable metrics export on clients targeting this endpoint.
Export metrics to a separate collector if required.

Example local receiver configuration under `management`:

```json
{"telemetry_ingest": {"auth_mode": "local"}}
```

Configure agent telemetry before starting its transports:

```python
import os

from mas_core.telemetry.runtime import TelemetryConfig, configure_telemetry

await configure_telemetry(
    TelemetryConfig(
        enabled=True,
        sample_ratio=1.0,
        otlp_endpoint="https://fleet.example:8443",
        export_metrics=False,
        headers={"Authorization": "Bearer " + os.environ["MAS_INGEST_TOKEN"]},
    )
)
```

The receiver broker can enable tracing with no exporter endpoint and retain its
local journal. Agent processes must export their spans to the receiver for
complete client-to-handler correlation. Broker tracing can export to a separate
collector while the dashboard consumes the broker's local journal. Use deployment
monitoring to check every broker's `/healthz` and deliver external notifications;
in-app alerts do not send notifications.

## Redis availability and durability

Example `gateway.yaml`:

```yaml
redis:
  sentinel:
    service_name: mas-primary
    addresses:
      - [redis-sentinel-1.example, 26379]
      - [redis-sentinel-2.example, 26379]
      - [redis-sentinel-3.example, 26379]
    tls: true
    ca_cert_path: /run/mas/redis/ca.pem
  socket_timeout: 5
  pool:
    max_connections: 512
    acquire_timeout_seconds: 5
  durability:
    replica_count: 1
    timeout_ms: 1000
    wait_for_aof: true
features:
  rbac: true
rate_limit:
  per_minute: 100000
  per_hour: 6000000
telemetry:
  enabled: true
  sample_ratio: 1.0
  otlp_endpoint: https://otel.example
  environment: production
audit:
  retention:
    max_messages: 100000
    max_security_events: 100000
    batch_size: 1000
    archive_directory: /var/lib/mas/audit-archive
```

Supply Redis and Sentinel credentials through a protected configuration source;
each uses a separate username/password pair. For standalone managed failover,
use a `rediss://` primary endpoint instead of `sentinel`. Redis must use
`maxmemory-policy noeviction`. Enable AOF on primary and replicas; `WAITAOF`
requires Redis 7.2 or newer. The validated topology uses `appendfsync always`,
two replicas and three Sentinels with quorum two.
Standalone, primary and Sentinel discovery pools have an explicit bounded socket
budget, defaulting to 512 connections. Saturation awaits available capacity for
at most five seconds rather than immediately rejecting concurrent operations.
Configure `redis.pool` to match connection budgets and admission deadlines;
`max_connections` must be between 1 and 65,536, and the acquisition timeout must
be finite and positive. Cancellation releases a waiting acquisition without
blocking the event loop. Socket timeouts remain a separate network limit.
The acceptance topology uses a two-second Sentinel failure threshold and disables
the five-second diskless full-sync batching delay. Use failure thresholds above
observed disk and event-loop stalls to avoid electing away a healthy primary.

`replica_count` defines acknowledgement requirements, not a consensus guarantee.
`WAITAOF` confirms local and requested replica persistence for preceding writes
on the same connection. Redis asynchronous failover can still lose writes in
failure combinations outside the tested primary-crash case. A timeout or
connection failure at confirmation returns an uncertain commit: preserve request
correlation, retry identical replies, and deduplicate business effects by message
ID. Never infer rollback from `UNAVAILABLE`.

ACL and RBAC grant access only after live-target and explicit-deny checks. To
require the RBAC path on every send, grant a role such as `send:worker` without an
ACL allow-list shortcut. Rate limits are per sending agent; size them for the
approved offered load. Slow handlers, payload size and network latency affect
capacity and must be included in your deployment's validation.

## Credentials and audit

Rotate gRPC credentials by writing a complete versioned directory and atomically
switching the `current` symlink. New handshakes load a valid certificate, matching
key and trust bundle. Existing RPC/transport authentication rechecks current
client trust and revoked certificate fingerprints. `revoked.json` is a JSON array
of 64-character SHA-256 certificate fingerprints. Missing or malformed current
trust/revocation policy fails closed. Invalid handshake rotation retains the last
valid handshake bundle while current application authentication enforces trust.
Restart the management HTTPS listener after rotating its certificate files.

Audit v2 chains bind each record to its predecessor. Retention archives immutable,
fsynced segments before trimming the corresponding Redis prefix and records a
checkpoint anchor. Bounded audit batches amortize storage confirmations while
each caller waits for its receipt. Broker shutdown drains pending batches before
closing Redis. The broker's normal non-reply send appends its audit record and
delivery stream entry atomically. This requires audit and routing to share their
commit target: the connection pool and the same durability policy instance.
Independently composed modules keep their configured backends and confirmation
policies, auditing before invoking their router. The successful ingress counter
and policy latency are
recorded after persistence confirmation; an uncertain write is never counted as
accepted. Keep archive storage shared by brokers, durable, backed up and
access controlled.
Without an archive directory, reaching the configured cap rejects further audit
writes rather than silently losing history. Legacy audit records remain readable
but do not receive a v2 integrity guarantee. Restore archive files and checkpoints
together with Redis. Integrity proofs require a trusted externally retained
anchor to detect wholesale deletion or replacement of all history.

## Recovery and alerts

Back up a completed `BGSAVE` snapshot, checksum it, copy it to durable storage and
test restore regularly. Copying an in-progress AOF rewrite is unsafe; Redis 7+
AOF backups include the complete manifest and its referenced files. Restore into
a new isolated primary first, validate queues, state revisions, policy and audit,
then enable AOF and wait for persistence to finish before attaching replicas and
moving brokers. Snapshot recovery has the backup's point-in-time RPO.

On primary failure, let Sentinel elect the primary. Investigate unavailable
durability barriers and replica links before lowering acknowledgement policy.
On broker failure, reconnect the agent after ownership expiry, then watch pending
entries drain through reclaim. Delivery is at least once; duplicates are expected
around failures. Do not delete pending streams to clear an incident.

Alert on broker readiness failures, Redis disconnects and latency, growing queue
lag, stale pending entries, DLQ growth, policy denials, audit/archive failures,
OTLP export errors, disk saturation, replica lag and certificate expiry. Set queue
and latency thresholds using the measured normal workload. The initial acceptance
targets are 1,000 accepted messages/sec for 60 seconds with RBAC and exported
tracing, handler p95 below 300 ms, recovery within 10 seconds and no lost accepted
messages in the exercised failure scenarios.

Run `uv run python -m tools.validate_production` for the sustained gate and
`uv run pytest integration_tests/test_production_recovery.py` for real primary
crash and backup restoration. Keep the JSON result with host specifications,
versions and deployment settings as release evidence.

## Async core lifecycle

Core Redis operations, leases, durability confirmations, observation ingestion and
readers are awaitable. Telemetry bootstrap, draining and shutdown are also async:
`await configure_telemetry(...)`, `await runtime.drain_spans(...)`, and
`await runtime.shutdown()`. Blocking SDK setup/export teardown, retained-data
conversion and Redis, agent, broker, HTTPS listener and OIDC client TLS loading run
in workers. Per-RPC trust and revocation checks also run in workers and still
read current policy files on every invocation. Span creation
and local counter updates remain immediate in-memory operations; SDK callbacks
only queue finalized spans. Cancellation preserves consumed drain results and
shared shutdown cleanup. The Redis client factory creates lazy configuration;
it does not open a network connection.
