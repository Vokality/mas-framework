# MSP Phase 0: Foundations

This document defines the shared implementation baseline for all later MSP phases.

## 1. Goal

Establish the package and app boundaries, shared identifiers, authoritative contracts, canonical state machines, and cross-cutting implementation rules that every later phase must reuse without reinterpretation.

## 2. In Scope

- New package boundaries for MSP functionality
- New app boundaries for the ops plane
- Shared identifier definitions
- Shared logical agent ids
- Shared contract ownership and canonical field sets
- Shared state machines
- Cross-cutting stack choices
- OpenAPI client generation policy
- Test harness expectations for integration and PydanticAI evaluation work

## 3. Out Of Scope

- Human-facing API route implementation details beyond ownership boundaries
- Device-specific normalization rules
- Incident cockpit UX behavior
- Approval workflow behavior beyond canonical state definitions
- Config apply workflow behavior beyond canonical state definitions
- Linux and Windows integration details

## 4. Package And App Changes

| Component | Type | Responsibility | Direct dependencies |
| --- | --- | --- | --- |
| `packages/mas-msp-contracts` | package | Shared Pydantic models for inter-agent and ops-plane contracts | `mas-core`, `pydantic` |
| `packages/mas-msp-core` | package | Deterministic workflow services and MAS agents | `mas-agent`, `mas-msp-contracts`, `mas-core` |
| `packages/mas-msp-ai` | package | Fabric-local PydanticAI orchestration and reasoning | `mas-agent`, `mas-msp-contracts`, `mas-msp-core`, `pydantic-ai` |
| `packages/mas-msp-network` | package | Network ingest, polling, diagnostics, and later executors | `mas-agent`, `mas-msp-contracts`, `mas-msp-core` |
| `packages/mas-msp-hosts` | package | Linux and Windows ingest, polling, diagnostics, and executors | `mas-agent`, `mas-msp-contracts`, `mas-msp-core` |
| `apps/mas-ops-api` | app | Human auth, Postgres read models, chat session API, approval API, config API, SSE streams, fabric connectors | `mas-msp-contracts`, `mas-msp-core`, FastAPI, Postgres client |
| `apps/mas-ops-ui` | app | Browser UI for portfolio, client drill-in, incident cockpit, approvals, config, and chat | React, TypeScript, generated OpenAPI client |

Reserved logical agent ids:

| Agent id | Owner | Phase introduced |
| --- | --- | --- |
| `ops-bridge` | `mas-msp-core` | Phase 2 |
| `inventory` | `mas-msp-core` | Phase 2 |
| `network-event-ingest` | `mas-msp-network` | Phase 2 |
| `network-polling` | `mas-msp-network` | Phase 2 |
| `core-orchestrator` | `mas-msp-ai` | Phase 3 |
| `network-diagnostics` | `mas-msp-network` | Phase 3 |
| `notifier-transport` | `mas-msp-core` | Phase 3 |
| `approval-controller` | `mas-msp-core` | Phase 4 |
| `config-deployer` | `mas-msp-core` | Phase 4 |
| `linux-event-ingest` | `mas-msp-hosts` | Phase 5 |
| `linux-polling` | `mas-msp-hosts` | Phase 5 |
| `linux-diagnostics` | `mas-msp-hosts` | Phase 5 |
| `linux-executor` | `mas-msp-hosts` | Phase 5 |
| `windows-event-ingest` | `mas-msp-hosts` | Phase 5 |
| `windows-polling` | `mas-msp-hosts` | Phase 5 |
| `windows-diagnostics` | `mas-msp-hosts` | Phase 5 |
| `windows-executor` | `mas-msp-hosts` | Phase 5 |
| `ops-plane-{client_id}` | `mas-ops-api` | Phase 2 |

The `ops-plane-{client_id}` connector id is the only ops-plane agent identity allowed to connect to a given client fabric from the central ops plane.

## 5. Public Interfaces And Contracts

Shared identifier definitions:

| Identifier | Format | Owner | Generation point | Stability |
| --- | --- | --- | --- | --- |
| `client_id` | UUIDv4 string | ops plane | created when a managed client is enrolled | stable across the client lifetime |
| `fabric_id` | UUIDv4 string | client fabric deployment | created when a MAS fabric is provisioned | stable for one deployed fabric |
| `incident_id` | UUIDv4 string | `core-orchestrator` | created when a new incident is opened | stable across the incident lifetime |
| `asset_id` | UUIDv4 string | `inventory` | created when an asset record is first persisted | stable for the asset lifetime |
| `approval_id` | UUIDv4 string | `approval-controller` | created when a write action enters approval | stable across the approval lifetime |
| `chat_session_id` | UUIDv4 string | ops plane | created when the UI opens a global or incident chat | stable across the chat session lifetime |
| `config_apply_run_id` | UUIDv4 string | ops plane | created when validation or apply is requested | stable for one validation/apply run |
| `event_id` | UUIDv4 string | event producer | created for each `PortfolioEvent` | unique per emitted event |

All identifiers are serialized as lowercase hyphenated UUIDv4 strings. Timestamp ordering is handled by explicit UTC timestamp fields rather than by sortable ids.

Authoritative shared contract set:

| Contract | Owner package | Canonical fields | Purpose |
| --- | --- | --- | --- |
| `AssetRef` | `mas-msp-contracts` | `asset_id`, `client_id`, `fabric_id`, `asset_kind`, `vendor`, `model`, `hostname`, `mgmt_address`, `site`, `tags` | stable reference to a managed asset |
| `CredentialRef` | `mas-msp-contracts` | `credential_ref`, `provider_kind`, `scope`, `purpose`, `secret_path` | reference to a secret without secret material |
| `HealthSnapshot` | `mas-msp-contracts` | `snapshot_id`, `client_id`, `fabric_id`, `asset`, `source_kind`, `collected_at`, `health_state`, `metrics`, `findings` | normalized polling or health state payload |
| `AlertRaised` | `mas-msp-contracts` | `alert_id`, `client_id`, `fabric_id`, `asset`, `source_kind`, `occurred_at`, `severity`, `category`, `title`, `normalized_facts`, `raw_reference` | normalized push alert payload |
| `DiagnosticsCollect` | `mas-msp-contracts` | `request_id`, `incident_id`, `client_id`, `fabric_id`, `asset`, `diagnostic_profile`, `requested_actions`, `timeout_seconds`, `read_only` | deterministic diagnostics request |
| `DiagnosticsResult` | `mas-msp-contracts` | `request_id`, `incident_id`, `client_id`, `fabric_id`, `asset`, `completed_at`, `outcome`, `evidence_bundle_id`, `observations`, `structured_results` | diagnostics response payload |
| `RemediationExecute` | `mas-msp-contracts` | `request_id`, `incident_id`, `client_id`, `fabric_id`, `asset`, `action_type`, `parameters`, `approval_id` | deterministic executor request |
| `RemediationResult` | `mas-msp-contracts` | `request_id`, `incident_id`, `client_id`, `fabric_id`, `asset`, `completed_at`, `outcome`, `audit_reference`, `post_state` | executor response payload |
| `IncidentRecord` | `mas-msp-contracts` | `incident_id`, `client_id`, `fabric_id`, `state`, `severity`, `summary`, `asset_ids`, `opened_at`, `updated_at` | persisted incident envelope |
| `EvidenceBundle` | `mas-msp-contracts` | `evidence_bundle_id`, `incident_id`, `asset_id`, `collected_at`, `items`, `summary` | structured evidence collected for an incident |
| `PortfolioEvent` | `mas-msp-contracts` | `event_id`, `client_id`, `fabric_id`, `event_type`, `subject_type`, `subject_id`, `occurred_at`, `payload_version`, `payload` | normalized event emitted from a fabric into the ops plane |
| `OperatorChatRequest` | `mas-msp-contracts` | `request_id`, `chat_session_id`, `turn_id`, `scope`, `actor_user_id`, `allowed_client_ids`, `client_id`, `fabric_id`, `incident_id`, `asset_ids`, `message` | operator message sent from the ops plane into a fabric |
| `OperatorChatResponse` | `mas-msp-contracts` | `request_id`, `chat_session_id`, `turn_id`, `state`, `incident_id`, `markdown_summary`, `evidence_bundle_ids`, `approval_id`, `recommended_actions` | operator-facing response returned from fabric-local reasoning |
| `IncidentChatContext` | `mas-msp-contracts` | `chat_session_id`, `incident_id`, `client_id`, `fabric_id`, `asset_ids`, `incident_state`, `recent_evidence_bundle_ids` | immutable context passed into incident-scoped chat turns |
| `ApprovalRequested` | `mas-msp-contracts` | `approval_id`, `client_id`, `fabric_id`, `incident_id`, `action_kind`, `title`, `requested_at`, `expires_at`, `requested_by_agent`, `payload`, `risk_summary` | approval request record |
| `ApprovalDecision` | `mas-msp-contracts` | `approval_id`, `decided_by_user_id`, `decision`, `decided_at`, `reason` | human approval decision |
| `ConfigDesiredState` | `mas-msp-contracts` | `client_id`, `fabric_id`, `desired_state_version`, `tenant_metadata`, `policy`, `inventory_sources`, `notification_routes` | declarative configuration record |
| `ConfigValidationResult` | `mas-msp-contracts` | `config_apply_run_id`, `client_id`, `desired_state_version`, `status`, `errors`, `warnings`, `validated_at` | validation output |
| `ConfigApplyRequested` | `mas-msp-contracts` | `config_apply_run_id`, `client_id`, `desired_state_version`, `requested_by_user_id`, `requested_at` | config apply request |
| `ConfigApplyResult` | `mas-msp-contracts` | `config_apply_run_id`, `client_id`, `desired_state_version`, `status`, `started_at`, `completed_at`, `error_summary` | config apply result |

Shared contract rules:

- Every shared contract is a Pydantic model defined in `mas-msp-contracts`.
- Every shared contract has an explicit `payload_version` only when used inside `PortfolioEvent.payload`.
- Contract additions must be backward compatible within the same `payload_version`.
- Breaking changes require a new `payload_version` and explicit consumer rollout.
- Raw device payloads are never embedded directly in shared contracts. Use `raw_reference` or structured facts only.

## 6. Data Model And State Transitions

Shared enums:

- `asset_kind`: `network_device`, `linux_host`, `windows_host`
- `health_state`: `healthy`, `degraded`, `critical`, `unknown`
- `severity`: `info`, `warning`, `minor`, `major`, `critical`
- `chat_scope`: `global`, `incident`

Canonical incident state machine:

| State | Allowed next states | Meaning |
| --- | --- | --- |
| `open` | `investigating`, `resolved` | incident was created but follow-up work has not started |
| `investigating` | `awaiting_approval`, `resolved`, `closed` | evidence is being collected or reasoned over |
| `awaiting_approval` | `investigating`, `remediating`, `closed` | a write action is blocked on human approval |
| `remediating` | `investigating`, `resolved`, `closed` | an approved write action is in progress |
| `resolved` | `closed` | impact has ended and no further action is required |
| `closed` | none | terminal archival state |

Canonical chat turn state machine:

| State | Allowed next states | Meaning |
| --- | --- | --- |
| `running` | `waiting_for_approval`, `completed`, `failed`, `cancelled` | the turn is currently executing |
| `waiting_for_approval` | `running`, `failed`, `cancelled` | the turn is paused behind a deferred approval |
| `completed` | none | terminal success |
| `failed` | none | terminal error |
| `cancelled` | none | terminal operator or system cancellation |

Canonical approval state machine:

| State | Allowed next states | Meaning |
| --- | --- | --- |
| `pending` | `approved`, `rejected`, `expired`, `cancelled` | approval is awaiting a human decision |
| `approved` | `executed`, `cancelled` | approval was granted and can be consumed |
| `rejected` | none | terminal rejection |
| `expired` | none | terminal expiry without a decision |
| `cancelled` | none | terminal cancellation before execution |
| `executed` | none | approval has been consumed by a write action |

Canonical config apply state machine:

| State | Allowed next states | Meaning |
| --- | --- | --- |
| `pending` | `validating`, `cancelled` | run exists but no validation has started |
| `validating` | `applying`, `failed`, `cancelled` | deterministic validation is in progress |
| `applying` | `succeeded`, `failed` | configuration changes are being applied |
| `succeeded` | none | terminal success |
| `failed` | none | terminal failure |
| `cancelled` | none | terminal cancellation before apply started |

Cross-phase invariants:

- A later phase may not add a new state to these machines without updating this document.
- A later phase may not interpret the same state name differently.
- A cancelled config run may only occur before the first mutation. Once a run enters `applying`, it must terminate as `succeeded` or `failed`.
- `ApprovalDecision.decision` values are `approve` and `reject`. `expired` and `cancelled` are system outcomes, not user decisions.

## 7. Request/Data Flow

The shared end-to-end model is:

1. A device or host emits data into a fabric-local edge agent.
2. The edge agent normalizes that data into Phase 0 contracts.
3. Fabric-local deterministic agents update inventory, state, and projections.
4. Fabric-local reasoning agents consume structured state and invoke deterministic tools when additional work is needed.
5. `OpsBridgeAgent` emits normalized `PortfolioEvent` records to the per-client ops-plane connector.
6. `mas-ops-api` stores those events into Postgres projection tables and exposes them through REST and SSE.
7. The browser reads only from `mas-ops-api`.
8. Human commands flow back from the browser to `mas-ops-api`, then through the per-client connector to `OpsBridgeAgent`, and then into fabric-local agents.

The browser never connects directly to:

- a MAS server
- Redis
- a managed device
- a secret store

## 8. Security And Authorization Rules

- One MAS fabric is deployed per client.
- All MAS agent connectivity remains mTLS-authenticated.
- `mas-ops-api` uses one least-privilege connector identity per client fabric: `ops-plane-{client_id}`.
- `OpsBridgeAgent` is the only fabric-local MAS agent that may communicate with the per-client ops-plane connector.
- Shared contracts must not include raw secret values.
- LLM-driven agents may not talk directly to devices or resolve credentials.
- Write actions must always pass through approval and deterministic executor agents.
- Global chat may only use ops-plane read models. Device access and diagnostics dispatch are incident-scoped fabric-local operations.

## 9. Acceptance Criteria

- Every later phase can reference this document for shared ids, agent ids, contracts, and state machines without redefining them.
- Shared contract names and canonical field sets are fixed.
- Shared state machines are fixed and explicit.
- Package and app boundaries are fixed.
- The implementation order is clear and non-circular.
- The stack defaults are fixed: Python, PydanticAI, FastAPI, React, Postgres, SSE, local auth first.

## 10. Test Plan

- Unit tests validate every shared Pydantic model against example payloads.
- Unit tests validate every shared state machine transition matrix.
- Contract serialization tests verify UTC timestamp encoding and UUID string handling.
- OpenAPI generation tests in later phases must consume these contract names without renaming them.
- Integration test harnesses must run a per-client MAS fabric and an ops-plane API against a real Postgres test database.
- PydanticAI eval fixtures must be stored in version-controlled test data rather than in ad hoc prompt files outside the repo.

## 11. Assumptions Locked By This Phase

- Shared identifiers use UUIDv4 rather than sortable ids.
- Shared timestamps use explicit UTC fields rather than implicit event order.
- `ops-plane-{client_id}` is the reserved connector identity format.
- `OpsBridgeAgent` belongs to `mas-msp-core`.
- Global chat is an ops-plane concern and incident chat is a fabric-local concern.
- `packages/mas-msp-hosts` is reserved in Phase 0 even though it is implemented later.
