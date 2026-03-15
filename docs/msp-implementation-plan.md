# MSP Implementation Plan

This document is the entrypoint for the phased MSP engineering doc set.

Reference documents:

- [Current architecture](./architecture.md)
- [Target MSP architecture](./msp-architecture.md)

The phased docs below are the implementation source of truth for the MSP program. They are written to be implemented in order without redefining shared contracts or changing the orchestration model midstream.

## 1. Locked Architecture Decisions

- MAS remains Python-based and keeps the current control-plane model from [architecture.md](./architecture.md).
- One MAS fabric is deployed per client.
- The MSP admin surface is a web application, not a desktop application.
- The central ops plane is implemented with:
  - `apps/mas-ops-api`
  - `apps/mas-ops-ui`
- The ops API uses FastAPI, Postgres, and SSE.
- The UI uses React, TypeScript, and Vite.
- PydanticAI is the required LLM orchestration layer for fabric-local reasoning.
- Browser clients talk only to `mas-ops-api`.
- `mas-ops-api` talks to client fabrics only through least-privilege MAS connector agents and the fabric-local `OpsBridgeAgent`.
- Device communication remains deterministic and fabric-local.
- All write actions require explicit human approval.
- Phase 0 owns shared identifiers, contracts, package boundaries, and state machines.

## 2. Doc Map

| Document | Role | Owns |
| --- | --- | --- |
| [architecture.md](./architecture.md) | Reference | Current repo and runtime truth |
| [msp-architecture.md](./msp-architecture.md) | Reference | Target topology, trust boundaries, and MSP deployment model |
| [msp-phase-0-foundations.md](./msp-phase-0-foundations.md) | Phase spec | Shared contracts, IDs, state machines, package boundaries, cross-phase invariants |
| [msp-phase-1-ops-plane.md](./msp-phase-1-ops-plane.md) | Phase spec | Human-facing API, UI behavior, auth model, Postgres models, SSE model |
| [msp-phase-2-network-visibility.md](./msp-phase-2-network-visibility.md) | Phase spec | Fabric bridge, inventory, network ingest, polling, visibility projections |
| [msp-phase-3-incident-cockpit.md](./msp-phase-3-incident-cockpit.md) | Phase spec | Incident chat, diagnostics orchestration, PydanticAI reasoning, cockpit UX |
| [msp-phase-4-approvals-and-config.md](./msp-phase-4-approvals-and-config.md) | Phase spec | Approval gating, desired-state config, validation and apply workflows |
| [msp-phase-5-host-observability-and-remediation.md](./msp-phase-5-host-observability-and-remediation.md) | Phase spec | Linux and Windows support, host diagnostics, typed remediation |

## 3. Phase Dependencies

| Phase | Depends on | Produces |
| --- | --- | --- |
| Phase 0 | Current MAS packages and reference docs | Shared implementation baseline |
| Phase 1 | Phase 0 | Multi-user ops plane, auth, UI shell, Postgres models, SSE transport |
| Phase 2 | Phase 0, Phase 1 | Fabric-to-ops visibility for Cisco and FortiGate devices |
| Phase 3 | Phase 0, Phase 1, Phase 2 | Incident cockpit, global chat, incident chat, diagnostics loop |
| Phase 4 | Phase 0, Phase 1, Phase 2, Phase 3 | Approval gating and desired-state config apply |
| Phase 5 | Phase 0, Phase 1, Phase 2, Phase 3, Phase 4 | Linux and Windows observability plus typed remediation |

## 4. Cross-Cutting Defaults

- All new docs stay directly under `docs/`.
- Shared contracts live in `packages/mas-msp-contracts`.
- Deterministic workflow agents live in `packages/mas-msp-core`.
- LLM-driven fabric-local orchestration lives in `packages/mas-msp-ai`.
- Network integrations live in `packages/mas-msp-network`.
- Host integrations live in `packages/mas-msp-hosts`.
- Frontend API typing is generated from FastAPI OpenAPI. The UI does not hand-maintain duplicate request or response types.
- All timestamps are UTC.
- All shared identifiers use lowercase hyphenated UUIDv4 strings.
- Raw secrets never appear in UI payloads, Postgres records, or MAS messages.
- Shared contract definitions are versioned additively. Breaking contract changes require a new payload version and a new consumer rollout.

## 5. Source Of Truth

- [architecture.md](./architecture.md) owns the current implementation and should not be edited to describe future MSP behavior.
- [msp-architecture.md](./msp-architecture.md) owns the desired topology and trust model.
- [msp-phase-0-foundations.md](./msp-phase-0-foundations.md) owns:
  - shared identifiers
  - logical agent ids
  - shared contracts
  - shared state machines
  - package and app boundaries
  - stack defaults
- Later phase docs must reference Phase 0 for shared contracts and state names instead of redefining them.
- [msp-phase-1-ops-plane.md](./msp-phase-1-ops-plane.md) owns the human-facing API, UI route map, auth model, Postgres schema groups, and SSE transport model.
- [msp-phase-2-network-visibility.md](./msp-phase-2-network-visibility.md) owns network-first ingest, polling, visibility projections, and `OpsBridgeAgent`.
- [msp-phase-3-incident-cockpit.md](./msp-phase-3-incident-cockpit.md) owns incident-scoped reasoning, diagnostics dispatch, cockpit behavior, and incident chat.
- [msp-phase-4-approvals-and-config.md](./msp-phase-4-approvals-and-config.md) owns approval semantics, config desired state, validation, and apply behavior.
- [msp-phase-5-host-observability-and-remediation.md](./msp-phase-5-host-observability-and-remediation.md) owns Linux and Windows observability, host diagnostics, host executors, and remediation history.

## 6. Implementation Order

1. Implement Phase 0.
2. Implement Phase 1.
3. Implement Phase 2.
4. Implement Phase 3.
5. Implement Phase 4.
6. Implement Phase 5.

Later phases may scaffold code early, but they must not change shared contracts or state machines outside the owning phase doc.
