# MSP Phase 4: Approvals And Config

This document defines write gating and desired-state configuration. Shared contracts, ids, and state machines are owned by [Phase 0](./msp-phase-0-foundations.md). Ops-plane routes and UI shells are owned by [Phase 1](./msp-phase-1-ops-plane.md). Incident orchestration is owned by [Phase 3](./msp-phase-3-incident-cockpit.md).

## 1. Goal

Deliver deterministic approval gating for all write actions and a desired-state configuration workflow with validation, apply, audit, and operator-visible run tracking.

## 2. In Scope

- `ApprovalController`
- `ConfigDeployerAgent`
- Approval request, decision, expiry, and resume semantics
- Desired-state config ownership in Postgres
- Validation and apply lifecycle
- Audit requirements for write actions and config changes
- UI and API behavior for pending, failed, succeeded, and cancelled runs

## 3. Out Of Scope

- SSO or external identity providers
- Secret value creation or storage in the UI
- Arbitrary shell execution
- Host-specific executors beyond the shared approval model
- Multi-step rollback workflows

## 4. Package And App Changes

`packages/mas-msp-core` changes:

| Component | Responsibility |
| --- | --- |
| `ApprovalController` | own approval records, expiry handling, decision application, and executor handoff |
| `ConfigDeployerAgent` | validate and apply declarative config into a client fabric |

`packages/mas-msp-ai` changes:

| Component | Responsibility |
| --- | --- |
| `CoreOrchestratorAgent` | create approval requests for proposed write actions and resume after approval decisions |

`apps/mas-ops-api` changes:

| Component | Responsibility |
| --- | --- |
| `approvals` | persist approvals, list them, and submit human decisions |
| `config` | persist desired-state config and create validation/apply runs |
| `audit` | persist operator-visible audit entries tied to approvals and config changes |

`apps/mas-ops-ui` changes:

| Screen area | Responsibility |
| --- | --- |
| approvals inbox | list pending, approved, rejected, expired, cancelled, and executed approvals |
| incident cockpit | surface approval banners and decision affordances |
| config console | edit desired state, validate it, apply it, and review run history |

## 5. Public Interfaces And Contracts

Phase 4 consumes and emits these Phase 0 contracts:

- `ApprovalRequested`
- `ApprovalDecision`
- `ConfigDesiredState`
- `ConfigValidationResult`
- `ConfigApplyRequested`
- `ConfigApplyResult`
- `RemediationExecute`
- `RemediationResult`

Approval rule:

- Every write action requires an `ApprovalRequested` record before execution.
- Read-only diagnostics do not require approval.

Write categories that require approval:

- network executor actions
- host executor actions
- desired-state config apply

Approval API behavior:

- `GET /approvals` returns approvals visible to the current user.
- `POST /approvals/{approval_id}/decision` accepts exactly one decision per pending approval.
- Only `admin` and `operator` may submit approval decisions.

Config API behavior:

- `GET /clients/{client_id}/config/desired-state` returns the latest desired-state record.
- `PUT /clients/{client_id}/config/desired-state` fully replaces the desired-state record.
- `POST /clients/{client_id}/config/validate` creates a validation run without mutating the fabric.
- `POST /clients/{client_id}/config/apply` creates an apply run against the current desired-state version.
- `ConfigValidationResult.status` uses `valid` or `invalid`.
- `ConfigApplyResult.status` uses the Phase 0 config apply states.

V1 desired-state config scope:

- tenant and fabric metadata
- policy
- inventory source definitions
- notification routing

V1 desired-state config exclusions:

- raw secret values
- executor action definitions
- arbitrary plugin configuration outside the supported scopes

## 6. Data Model And State Transitions

Approval record persistence:

- `approval_requests` stores one row per `approval_id`.
- `approval_requests.state` must follow the Phase 0 approval state machine.
- `approved_at`, `rejected_at`, `expired_at`, `cancelled_at`, and `executed_at` are nullable timestamps populated only for the matching terminal or consumption event.

Approval semantics:

- `pending -> approved` on a human approval decision.
- `pending -> rejected` on a human rejection decision.
- `pending -> expired` when `expires_at` passes without a decision.
- `pending -> cancelled` when the originating action is withdrawn before decision.
- `approved -> executed` when the approval token is consumed by exactly one executor action.
- `approved -> cancelled` if the system withdraws the action before execution begins.

Desired-state persistence:

- `config_desired_states` stores exactly one current desired-state document per `client_id`.
- The document includes `desired_state_version`, incremented by one on each successful `PUT`.
- `PUT` is optimistic on `desired_state_version` and must reject stale updates.

Validation and apply run semantics:

- Each validation or apply request creates a new `config_apply_run_id`.
- Validation runs are immutable records and do not mutate the fabric.
- Apply runs target one exact `desired_state_version`.
- A run in `pending` or `validating` may be cancelled.
- A run in `applying` may not be cancelled and must terminate as `succeeded` or `failed`.

Deterministic apply order:

1. tenant and fabric metadata
2. policy
3. inventory source definitions
4. notification routing

Any failure stops the apply run immediately. Partial apply state is reported in the run record and the audit trail.

## 7. Request/Data Flow

Approval flow:

1. `CoreOrchestratorAgent` determines that a write action is required.
2. `CoreOrchestratorAgent` creates `ApprovalRequested`.
3. `ApprovalController` persists the request and exposes it through the ops plane.
4. The UI surfaces the pending approval in the incident cockpit and approvals inbox.
5. A human submits `ApprovalDecision`.
6. `ApprovalController` validates scope and state.
7. If approved, the originating deferred action is resumed.
8. The executor consumes the approval and records `executed`.

Desired-state config flow:

1. A human updates desired state through the config console.
2. `mas-ops-api` persists the new desired-state document and increments `desired_state_version`.
3. The user requests validation or apply.
4. `mas-ops-api` creates a run record and sends the request through the per-client connector to `ConfigDeployerAgent`.
5. `ConfigDeployerAgent` validates or applies in deterministic order.
6. Results are written back as `ConfigValidationResult` or `ConfigApplyResult`.
7. The UI receives run updates through REST refresh and SSE events.

## 8. Security And Authorization Rules

- `admin` may edit desired-state configuration.
- `operator` may not edit desired-state configuration.
- `admin` and `operator` may approve or reject write actions for authorized clients.
- Approval decisions must be rejected if the approval is not in `pending`.
- Approvals are single-use. Once executed, they may not be replayed.
- Config apply requests must target the latest desired-state version unless an explicit historical version endpoint is introduced later.
- Desired-state documents may contain only secret references, never secret values.
- Every approval and config run transition must emit an audit entry with actor, target, action, and outcome.

## 9. Acceptance Criteria

- Every write action is blocked until an approval is recorded.
- Read-only diagnostics remain approval-free.
- Approvals can be created, viewed, approved, rejected, expired, cancelled, and consumed.
- Desired-state configuration is stored in Postgres and versioned.
- Validation runs do not mutate the fabric.
- Apply runs mutate the fabric in deterministic order and expose terminal status.
- The UI shows approval status and config run status clearly in both REST and SSE-driven views.

## 10. Test Plan

- Unit tests for approval state transitions and expiry handling.
- Integration tests for approval creation, decision submission, executor resume, and approval consumption.
- API tests for config desired-state read, replace, validate, and apply routes.
- Integration tests for validation-only runs and apply runs.
- Audit tests for approval creation, decision, execution, desired-state replacement, validation, and apply.
- UI integration tests for approvals inbox, incident approval banners, config editor, validation results, and apply history.

## 11. Assumptions Locked By This Phase

- All write actions require approval without exception in v1.
- Desired-state configuration is owned by Postgres, not by checked-in files.
- `PUT` replaces the full desired-state document.
- Apply order is fixed and deterministic.
- Secret values are out of scope for direct UI entry in v1.
