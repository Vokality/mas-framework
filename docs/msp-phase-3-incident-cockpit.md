# MSP Phase 3: Incident Cockpit

This document defines incident reasoning, diagnostics dispatch, and operator chat. Shared contracts, ids, and state machines are owned by [Phase 0](./msp-phase-0-foundations.md). Ops-plane API and UI foundations are owned by [Phase 1](./msp-phase-1-ops-plane.md). Network visibility is owned by [Phase 2](./msp-phase-2-network-visibility.md).

## 1. Goal

Deliver the incident cockpit, fabric-local PydanticAI orchestration, global portfolio chat, incident-scoped chat, and a deterministic diagnostics loop that turns visibility data into evidence-backed operator guidance.

## 2. In Scope

- `CoreOrchestratorAgent`
- `SummaryComposer`
- `NetworkDiagnosticsAgent`
- Global chat behavior
- Incident-scoped chat behavior
- PydanticAI deps and output classes
- Deterministic orchestration tools
- Diagnostics dispatch and result handling
- Deferred execution for long-running diagnostics
- SSE streaming for chat and incident timelines
- Incident cockpit UI behavior

## 3. Out Of Scope

- Write execution
- Approval controller implementation
- Desired-state config apply
- Linux and Windows integrations
- Cross-client LLM device access

## 4. Package And App Changes

`packages/mas-msp-ai` changes:

| Component | Responsibility |
| --- | --- |
| `CoreOrchestratorAgent` | incident creation, triage, diagnostics planning, evidence-backed recommendations |
| `SummaryComposer` | incident summary drafting from structured evidence |
| `deps` | PydanticAI dependency objects passed into each run |
| `outputs` | PydanticAI typed output models |
| `toolsets` | deterministic tool wrappers for inventory, activity, diagnostics, and summary persistence |
| `durable` | deferred execution wrappers for long-running diagnostics |

`packages/mas-msp-network` changes:

| Component | Responsibility |
| --- | --- |
| `NetworkDiagnosticsAgent` | execute deterministic read-only diagnostics against Cisco and FortiGate devices |

`packages/mas-msp-core` changes:

| Component | Responsibility |
| --- | --- |
| `NotifierTransportAgent` | deliver incident summaries into operator-visible channels and activity streams |
| `OpsBridgeAgent` | route incident chat requests and responses between `mas-ops-api` and `core-orchestrator` |

`apps/mas-ops-api` changes:

| Component | Responsibility |
| --- | --- |
| `chat/portfolio_assistant` | global chat over Postgres portfolio read models only |
| `chat/fabric_bridge` | incident chat dispatch into the correct fabric through `OpsBridgeAgent` |
| `streams/chat` | chat and incident SSE streaming |

`apps/mas-ops-ui` changes:

| Screen area | Responsibility |
| --- | --- |
| incident cockpit | show incident summary, assets, evidence, activity, and chat |
| global chat | portfolio-only operator assistant |
| incident chat | incident-scoped conversation panel |

## 5. Public Interfaces And Contracts

Phase 3 consumes and emits these Phase 0 contracts:

- `IncidentRecord`
- `EvidenceBundle`
- `DiagnosticsCollect`
- `DiagnosticsResult`
- `OperatorChatRequest`
- `OperatorChatResponse`
- `IncidentChatContext`

PydanticAI deps classes:

| Class | Fields |
| --- | --- |
| `TriageDeps` | `client_id`, `fabric_id`, `incident_id`, `asset_refs`, `recent_activity`, `recent_evidence`, `user_intent`, `allowed_actions` |
| `DiagnosticsPlannerDeps` | `incident_id`, `asset_refs`, `recent_evidence`, `available_profiles`, `allowed_actions` |
| `SummaryComposerDeps` | `incident_id`, `incident_record`, `asset_refs`, `recent_evidence`, `recent_activity` |

PydanticAI output classes:

| Class | Fields |
| --- | --- |
| `TriageDecision` | `incident_id`, `summary`, `severity`, `next_action`, `diagnostic_profiles`, `rationale` |
| `DiagnosticsPlan` | `incident_id`, `steps`, `continue_in_background`, `operator_message` |
| `IncidentResponse` | `chat_session_id`, `turn_id`, `state`, `markdown_summary`, `evidence_bundle_ids`, `recommended_actions`, `approval_required` |
| `SummaryDraft` | `incident_id`, `headline`, `operator_summary`, `affected_assets`, `recommended_actions` |

Deterministic tools exposed to `CoreOrchestratorAgent`:

| Tool | Purpose |
| --- | --- |
| `get_incident_context` | load the current incident record and activity summary |
| `get_asset_context` | load resolved `AssetRef` records for the active incident |
| `get_recent_evidence` | load recent `EvidenceBundle` summaries |
| `request_diagnostics` | send `DiagnosticsCollect` to `network-diagnostics` |
| `append_activity` | persist an activity entry for operator-visible timelines |
| `persist_summary` | store the latest incident summary and recommended actions |

Global chat behavior:

- Global chat is handled by `mas-ops-api` against Postgres portfolio read models only.
- Global chat may summarize portfolio state, filter clients, surface notable incidents, and produce links into client or incident views.
- Global chat may not dispatch diagnostics or request write actions.

Incident-scoped chat behavior:

- Incident chat is handled by `CoreOrchestratorAgent` inside the selected client fabric.
- Incident chat inherits:
  - `client_id`
  - `fabric_id`
  - `incident_id`
  - active `asset_ids`
  - recent evidence
  - recent activity
- Incident chat may request read-only diagnostics through deterministic tools.

## 6. Data Model And State Transitions

Incident creation rules:

- `CoreOrchestratorAgent` creates a new `IncidentRecord` when visibility data or incident chat requires a durable incident that does not already exist.
- The initial state is `open`.
- The first active reasoning or diagnostics step moves the incident to `investigating`.

Chat persistence rules:

- `chat_sessions.scope = global` sessions are stored only in the ops plane.
- `chat_sessions.scope = incident` sessions are stored in the ops plane and linked to exactly one `incident_id`.
- `chat_turns.turn_id` is the correlation key for request, stream events, and final response persistence.

Deferred execution rules:

- Phase 3 implements deferred execution for diagnostics only.
- A chat turn enters `running` when submitted.
- If diagnostics are dispatched asynchronously, the turn stays `running` until a `DiagnosticsResult` arrives.
- The turn completes only after the orchestrator resumes and emits a typed `IncidentResponse`.
- Approval-backed deferred execution is introduced in Phase 4.

## 7. Request/Data Flow

Global chat flow:

1. The UI posts a message to `/chat/sessions/{chat_session_id}/messages` for a `global` session.
2. `mas-ops-api` loads the caller's authorized portfolio context from Postgres.
3. `portfolio_assistant` runs a PydanticAI turn against read models only.
4. `mas-ops-api` persists the turn and streams `chat.delta` and `chat.completed`.

Incident chat flow:

1. The UI posts a message to `/chat/sessions/{chat_session_id}/messages` for an `incident` session.
2. `mas-ops-api` builds `OperatorChatRequest` and sends it through the per-client connector to `OpsBridgeAgent`.
3. `OpsBridgeAgent` routes the request to `core-orchestrator`.
4. `CoreOrchestratorAgent` runs PydanticAI with incident context and deterministic tools.
5. If more evidence is required, `CoreOrchestratorAgent` calls `request_diagnostics`.
6. `NetworkDiagnosticsAgent` executes deterministic read-only diagnostics and returns `DiagnosticsResult`.
7. `CoreOrchestratorAgent` resumes, persists summary updates, and returns `OperatorChatResponse`.
8. `mas-ops-api` stores the turn result and streams `incident.updated`, `activity.appended`, and `chat.completed`.

Incident cockpit composition:

- header with incident state, severity, and summary
- affected asset panel
- evidence panel
- activity timeline
- incident chat panel
- recommendation panel

## 8. Security And Authorization Rules

- Global chat is limited to authorized clients and Postgres read models.
- Incident chat requires access to the incident's `client_id`.
- `CoreOrchestratorAgent` may invoke only deterministic read-only diagnostics in Phase 3.
- `NetworkDiagnosticsAgent` may not execute write actions.
- `mas-ops-api` may not fabricate incident chat responses. All incident-scoped reasoning comes from fabric-local orchestration.
- SSE streams for incident chat and incident activity must re-check client authorization on connect and reconnect.

## 9. Acceptance Criteria

- Global chat works over authorized portfolio data without fabric-local device access.
- Incident-scoped chat reaches `CoreOrchestratorAgent` through `OpsBridgeAgent`.
- `CoreOrchestratorAgent` can create or update an `IncidentRecord`.
- `CoreOrchestratorAgent` can request deterministic read-only diagnostics.
- `NetworkDiagnosticsAgent` returns structured evidence through `DiagnosticsResult`.
- The incident cockpit shows streaming chat output, incident summary updates, evidence, and activity in one view.

## 10. Test Plan

- PydanticAI evals for `TriageDecision`, `DiagnosticsPlan`, and `SummaryDraft`.
- Unit tests for deterministic tool wrappers.
- Integration tests for incident chat request routing through `OpsBridgeAgent`.
- Integration tests for diagnostics dispatch and resume behavior.
- API tests for global chat versus incident chat scope enforcement.
- UI integration tests for cockpit rendering, live streaming, evidence updates, and activity timeline updates.

## 11. Assumptions Locked By This Phase

- Global chat stays in the ops plane and uses only Postgres read models.
- Incident chat stays fabric-local and uses `CoreOrchestratorAgent`.
- Phase 3 diagnostics are read-only.
- Approval-backed pause and resume is deferred to Phase 4.
