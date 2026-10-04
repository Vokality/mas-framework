# MAS Framework

A runtime for building **multi-agent systems** in Python: independent agent
processes that exchange typed messages, hold durable state, and discover one
another — over a trusted broker that enforces identity, policy, and reliability
so your agent code doesn't have to.

Agents are clients. The `mas-server` broker owns all Redis access (routing,
state, audit, policy); agents connect over gRPC + mTLS and never receive storage
credentials. What you write is an agent's logic — message handlers and state
transitions. What you get around it: authenticated transport, request/reply
correlation, at-least-once delivery, data-loss prevention, rate limiting,
authorization, audit, and distributed tracing.

## Why it exists

Most "agent frameworks" orchestrate calls inside a single process. The moment
you run agents as *separate, long-lived, stateful services* — which is what real
multi-agent systems are — you inherit a pile of distributed-systems and security
problems: who may talk to whom, how a reply finds its caller, what happens when a
handler crashes mid-message, how you keep PII out of a log, how you trace one
request across five agents.

MAS treats those as the framework's job. The pieces below sit *in* the message
path, not bolted on beside it:

| Concern | Built in |
| --- | --- |
| **Identity & transport** | gRPC bidirectional streaming, mutual TLS, SPIFFE SAN identity |
| **Messaging** | fire-and-forget `send`, `request`/`reply` with correlation IDs + timeouts, capability-based `discover` |
| **Durable state** | per-agent Pydantic state model, persisted server-side, restored on restart |
| **Reliability** | at-least-once delivery, ACK/NACK, redelivery, dead-letter queue, in-flight limits, graceful-shutdown ACK draining |
| **Governance** | RBAC authorization, DLP (PII/secret scan + redaction), rate limiting, circuit breaking, hash-chained tamper-evident audit |
| **Observability** | Fleet dashboard, retained history, delivery p95, trace explorer, alerts and exporter health, OpenTelemetry traces and metrics |

A reply is bound to the agent the request targeted, so an unrelated agent can't
resolve your pending call by guessing a correlation id. Many instances can share
one `agent_id`; replies route back to the instance that asked.

## Installation

```bash
pip install mas-framework
```

This is a meta-package that pulls in everything.

For lighter installs you can pick individual components:

```bash
pip install mas-agent     # just the client
pip install mas-server    # just the broker
```

> **Note**: 0.5+ uses split packages (`from mas_agent import ...`, `from mas_server import ...`).
> The old monolithic `mas` namespace is no longer provided.

## The programming model

An agent is a class. You declare typed handlers and, optionally, typed state;
the framework handles the wire, the correlation, the acks, and the persistence.

```python
from mas_agent import Agent, AgentMessage, TlsClientConfig
from pydantic import BaseModel


class Ask(BaseModel):
    question: str


class DeskState(BaseModel):
    answered: int = 0


class HelpDesk(Agent[DeskState]):
    @Agent.on("ask", model=Ask)
    async def handle_ask(self, message: AgentMessage, payload: Ask) -> None:
        answer = await my_llm(payload.question)  # your logic
        await self.update_state({"answered": self.state.answered + 1})
        await self.send_reply_envelope(message, "answer", {"text": answer})


tls = TlsClientConfig(
    root_ca_path="ca.pem",
    client_cert_path="desk.pem",
    client_key_path="desk.key",
)
desk = HelpDesk("helpdesk", capabilities=["qa"], state_model=DeskState, tls=tls)
await desk.start()  # connects over mTLS, restores state, begins handling
```

Another agent calls it. Request/reply is one line, and the payload is validated
into your model *before* your handler runs:

```python
router = Agent("router", tls=tls)
await router.start()

matches = await router.discover(capabilities=["qa"])  # find agents by capability
reply = await router.request("helpdesk", "ask", {"question": "..."}, timeout=10)
print(reply.data["text"])
```

When `timeout` is omitted, request/reply uses a 60-second client and server
budget. The server stores pending correlation state in Redis for the same TTL.

A handler that raises is NACKed and retried up to `max_delivery_attempts`
(default 5), then preserved in the dead-letter queue. Invalid typed payloads
go directly to the dead-letter queue. Handlers should be idempotent
(dedupe on `message.message_id`). State is a typed model — an out-of-type update
is rejected, not silently persisted.

## Standing up the broker

The server is the trust boundary: it holds the Redis connection, the agent
registry, and the policy pipeline. Config flows down from here into every module.

```python
from mas_gateway import GatewaySettings
from mas_server import AgentDefinition, MASServer, MASServerSettings, TlsConfig

server = MASServer(
    settings=MASServerSettings(
        listen_addr="127.0.0.1:50051",
        tls=TlsConfig(
            server_cert_path="certs/server.pem",
            server_key_path="certs/server.key",
            client_ca_path="certs/ca.pem",
        ),
        agents={
            "helpdesk": AgentDefinition(
                agent_id="helpdesk", capabilities=["qa"], metadata={}
            ),
            "router": AgentDefinition(agent_id="router", capabilities=[], metadata={}),
        },
    ),
    gateway=GatewaySettings(),  # DLP, rate limits, circuit breaker, audit
)

await server.start()
await server.authz.set_permissions("router", allowed_targets=["helpdesk"])
```

`GatewaySettings` is the single place governance is configured — DLP rules, rate
limits, circuit-breaker thresholds, audit sinks — and it is injected down into
each module rather than re-read or re-instantiated anywhere.

## What you can build

- **Multi-agent / LLM systems** — planner/worker swarms, specialist pipelines,
  supervisor-and-critic loops, where each agent is its own typed, stateful service.
- **Regulated agent platforms** — healthcare, finance, and other domains where
  every message must be authenticated, authorized, scanned for sensitive data,
  rate-limited, and auditable.
- **Durable distributed workflows** — request/reply + durable state + at-least-once
  + DLQ for long-running, crash-tolerant orchestration.
- **A resilient agent mesh** — agents as services with discovery, circuit
  breaking, and rate limiting as first-class behavior.

The framework is the substrate; the agents' intelligence — LLM calls, business
logic — lives in your handlers. (`openai` and `pydantic-ai` are dev dependencies
because LLM-backed agents are the expected end use.)

## Packages

`pip install mas-framework` is the recommended way to get the full set (it's a meta-package).

Individual packages (for lighter or selective installs):

- `mas-proto` — protobuf contract and generated gRPC bindings.
- `mas-core` — shared envelope, JSON types, Redis client, and OpenTelemetry primitives.
- `mas-gateway` — authorization, DLP, rate limiting, circuit breaker, audit, and gateway config.
- `mas-server` — the gRPC broker runtime (routing, delivery, sessions, registry, policy).
- `mas-agent` — the agent client runtime.

You compose `MASServer`, `GatewaySettings`, and `Agent` from your own code.
The broker includes an optional management dashboard.

## Management dashboard

Enable the dashboard on your existing broker settings:

```python
from dataclasses import replace
from mas_server.management import ManagementSettings

settings = replace(settings, management=ManagementSettings(port=8080))
server = MASServer(settings=settings, gateway=gateway)
await server.start()
print(server.management_url)  # http://127.0.0.1:8080
```

The dashboard shows fleet health, live accepted traffic and delivery p95,
retained performance charts, trace waterfalls, active/resolved alerts and actual
exporter outcomes alongside local sessions, workers, queues, dead letters and
policy decisions. Broker heartbeats continue without a browser. Queue counts
use consumer-group lag and pending entries. Message payloads, state contents,
Redis credentials, and exporter headers are excluded.

`GET /api/snapshot` returns typed JSON; `GET /healthz` returns 503 when Redis or a
local delivery worker is unavailable. Snapshot reads are bounded and cached for
two seconds. Sessions are local to the broker; queue and audit data belong to its
Redis database. Fleet counters are scoped to individual broker incarnations and work with OTLP
disabled. Retained trace details are bounded and sampled; delivery latency
requires correlated client spans and synchronized clocks. Configure
`GatewaySettings.telemetry` for external
trace and metric export. A host may call `await get_telemetry().shutdown()` at process
teardown; stopping one broker leaves shared telemetry running.

The listener defaults to loopback. Use
`ManagementSettings(auth_mode="token", token=...)` for a local development token.
Remote listeners require `auth_mode="oidc"`, an explicitly configured issuer,
audience and read scope or role, and HTTPS certificate paths. The dashboard accepts
an operator's signed access token and holds it in browser memory. Operator APIs
are read-only; optional OTLP ingestion requires separate write authorization. See [operator configuration](docs/operations.md) for deployment,
credential rotation, storage durability and recovery procedures.

Production acceptance requires 1,000 accepted messages/sec for 60 seconds, handler
p95 below 300 ms, RBAC checks on every send, and 100% trace sampling with verified
OTLP export. Run `uv run python -m tools.validate_production` on your target host.
The [readiness record](docs/readiness.md) distinguishes tested behavior from
environment-specific deployment requirements.

To try the dashboard with real local message traffic:

```bash
uv run examples/control_room.py
```

The example uses Redis database 15 by default and generates temporary development
mTLS credentials. It does not flush Redis. Set `MAS_REDIS_URL` or use `--port` to
change its endpoints. Press Ctrl-C to stop the agents and broker.

## Running locally

```bash
docker compose up -d redis          # the broker's backing store
uv sync --all-groups --all-packages
uv run pytest
uv run ruff check . && uv run ty check
```
