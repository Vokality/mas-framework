# Current Architecture

This document reflects the package-only MAS system in this repository.

## 1. Repository And Package Architecture

```mermaid
flowchart TB
    root["Workspace Root<br/>pyproject.toml<br/>tool.uv.package = false<br/>tool.uv.workspace members = packages/*"]

    subgraph packages["Workspace Members"]
        proto["mas-proto<br/>RuntimeService protobuf contract<br/>Generated bindings"]
        core["mas-core<br/>Envelope, JSON validation,<br/>Redis client, telemetry"]
        gateway["mas-gateway<br/>Authorization, DLP, rate limits,<br/>circuit breaker, audit, config"]
        agent["mas-agent<br/>Agent client runtime<br/>mTLS gRPC client + typed handlers"]
        server["mas-server<br/>gRPC server runtime<br/>routing, delivery, sessions, registry, state"]
    end

    root --> proto
    root --> core
    root --> gateway
    root --> agent
    root --> server

    core --> gateway
    core --> agent
    proto --> agent
    core --> server
    gateway --> server
    proto --> server
```

## 2. Runtime Topology

```mermaid
flowchart LR
    subgraph host["Host Application"]
        app["Application entrypoint"]
        config["MASServerSettings<br/>GatewaySettings<br/>Agent definitions"]
    end

    subgraph control["Control Plane"]
        subgraph serverbox["MASServer"]
            grpc["RuntimeService<br/>gRPC + mTLS"]
            ingress["IngressService"]
            policy["PolicyPipeline"]
            authz["AuthorizationModule"]
            rate["RateLimitModule"]
            cb["CircuitBreakerModule"]
            dlp["DLPModule"]
            audit["AuditModule"]
            router["MessageRouter"]
            delivery["DeliveryService"]
            sessions["SessionManager"]
            registry["RegistryService"]
            state["StateStore"]
        end

        redis["Redis"]
        otel["OpenTelemetry Exporters<br/>(optional)"]
        management["Management dashboard + health API<br/>(optional HTTP listener)"]
    end

    subgraph agentside["Agent Processes"]
        a1["Agent instance A<br/>mas_agent.Agent"]
        a2["Agent instance B<br/>mas_agent.Agent"]
    end

    app --> config
    config --> grpc
    a1 <-->|"RuntimeService RPCs + Transport stream<br/>mTLS / SPIFFE SAN identity"| grpc
    a2 <-->|"RuntimeService RPCs + Transport stream<br/>mTLS / SPIFFE SAN identity"| grpc

    grpc --> ingress
    grpc --> sessions
    grpc --> registry
    grpc --> state

    ingress --> policy
    policy --> authz
    policy --> rate
    policy --> cb
    policy --> dlp
    policy --> audit
    policy --> router

    delivery --> sessions
    delivery --> router
    delivery --> cb
    delivery --> redis

    registry --> redis
    state --> redis
    router --> redis
    audit --> redis
    authz --> redis
    rate --> redis
    cb --> redis
    ingress --> redis

    management --> sessions
    management --> redis
    management --> otel

    grpc --> otel
    ingress --> otel
    policy --> otel
    delivery --> otel
    a1 --> otel
    a2 --> otel
```

## 3. Data And Control Paths

```mermaid
sequenceDiagram
    participant Sender as Sender Agent
    participant Server as RuntimeService / MASServer
    participant Policy as PolicyPipeline
    participant Redis as Redis
    participant Target as Target Agent

    Sender->>Server: Send / Request / Reply / Discover / State RPC
    Server->>Policy: Build EnvelopeMessage and evaluate ingress
    Policy->>Redis: Check authorization, rate limit and circuit state

    alt one-way send
        Policy->>Redis: Atomic audit + XADD agent.stream:{target_id}
    else request/reply
        Server->>Redis: SET mas.pending_request:{correlation_id} EX ttl
        Policy->>Redis: Atomic audit + request enqueue, then persistence confirmation
        Note over Redis,Target: Reply path uses agent.stream:{origin_agent}:{origin_instance}
        Note over Policy,Redis: Reply enqueue + receipt + pending consumption commit atomically
    end

    Redis-->>Server: XREADGROUP shared + instance streams
    Server-->>Target: Transport delivery event
    Target-->>Server: ACK or NACK

    alt ACK
        Server->>Redis: Atomically XACK and XDEL owned entry
    else retryable NACK
        Server->>Redis: Atomically XADD attempt+1, XACK and XDEL owned entry
    else non-retryable NACK or retry budget exhausted
        Server->>Redis: XADD dlq:messages, then XACK only if DLQ write succeeds
    end

    Target->>Server: GetState / UpdateState / ResetState
    Server->>Redis: Atomic state snapshot / revision compare-and-set / reset
```

## 4. Redis Data Model

```mermaid
flowchart TB
    redis["Redis"]

    streams["Delivery Streams<br/>agent.stream:{agent_id}<br/>agent.stream:{agent_id}:{instance_id}"]
    pending["Pending Request Map<br/>mas.pending_request:{correlation_id}"]
    state["Agent State Hash<br/>agent.state:{agent_id}"]
    audit["Audit Streams<br/>audit:messages<br/>audit:by_sender:{sender_id}<br/>audit:by_target:{target_id}<br/>audit:last_hash"]
    dlq["Dead Letter Queue<br/>dlq:messages"]
    registry["Agent Registry Hashes<br/>agent:{agent_id}"]
    acl["ACL Sets<br/>agent:{agent_id}:allowed_targets<br/>agent:{agent_id}:blocked_targets"]

    redis --> streams
    redis --> pending
    redis --> state
    redis --> audit
    redis --> dlq
    redis --> registry
    redis --> acl
```

## 5. Current System Boundaries

- Agents never connect to Redis directly.
- All inter-agent communication flows through `RuntimeService` on the MAS server over gRPC with mandatory mTLS.
- Agent identity is derived from the client certificate SPIFFE URI SAN: `spiffe://mas/agent/{agent_id}`.
- Authorization is deny-by-default and enforced server-side.
- Discovery is permission-scoped and only returns active allowlisted agents.
- Agent state is per logical `agent_id`, so multiple instances of the same agent share persisted state.
- Shared work distribution uses `agent.stream:{agent_id}` with Redis consumer groups.
- Replies are pinned to the requesting process using `agent.stream:{agent_id}:{instance_id}`.
- The workspace root is not a distributable package; installable artifacts are the individual workspace members.

## 6. Management and telemetry

`mas_server.management.ManagementSettings` enables the bundled dashboard on a
separate loopback HTTP listener. Remote listeners require scoped OIDC access and HTTPS. The
read-only snapshot includes typed health, local session/worker state, shared
Redis queue lag/pending counts, DLQ count, latest policy decisions, circuit status,
and bounded process telemetry counters. Operational metadata excludes payloads,
agent state, and credentials. Redis reads use a timeout, a 500-stream coverage
limit, pipelining, and a two-second snapshot cache. Partial or unavailable data is
marked degraded; unknown queue/DLQ counts are never substituted with zero.
