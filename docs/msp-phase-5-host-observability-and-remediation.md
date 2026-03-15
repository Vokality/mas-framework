# MSP Phase 5: Host Observability And Remediation

This document defines Linux and Windows support, plus typed host remediation. Shared contracts, ids, and state machines are owned by [Phase 0](./msp-phase-0-foundations.md). Incident orchestration is owned by [Phase 3](./msp-phase-3-incident-cockpit.md). Approval gating is owned by [Phase 4](./msp-phase-4-approvals-and-config.md).

## 1. Goal

Extend the MSP model from network devices to Linux and Windows servers while preserving the same orchestration boundary: deterministic edge integrations, fabric-local reasoning, approval-gated typed execution, and operator-visible history.

## 2. In Scope

- `packages/mas-msp-hosts`
- Linux first, then Windows
- Linux event ingest, polling, diagnostics, and executor support
- Windows event ingest, polling, diagnostics, and executor support
- Host-specific UI extensions
- Typed host remediation contracts and executor boundaries
- Remediation history and post-execution evidence handling

## 3. Out Of Scope

- Arbitrary shell execution
- Arbitrary PowerShell execution
- Package installation or unbounded OS patching
- Domain-wide Windows administration outside constrained host scopes
- Cross-client host automation from the ops plane

## 4. Package And App Changes

`packages/mas-msp-hosts` must introduce these agents:

| Agent id | Responsibility | Order |
| --- | --- | --- |
| `linux-event-ingest` | ingest Linux host events from journald or syslog sources | first |
| `linux-polling` | collect Linux host health and service summaries | first |
| `linux-diagnostics` | execute read-only Linux diagnostics | first |
| `linux-executor` | execute typed Linux host remediations | first |
| `windows-event-ingest` | ingest Windows host events from WEF | second |
| `windows-polling` | collect Windows host health and service summaries | second |
| `windows-diagnostics` | execute read-only Windows diagnostics | second |
| `windows-executor` | execute typed Windows host remediations | second |

`apps/mas-ops-ui` must extend:

| Screen area | Responsibility |
| --- | --- |
| asset detail | host health panels, service status, event evidence, remediation history |
| incident cockpit | host-specific diagnostics summaries and remediation timeline entries |

## 5. Public Interfaces And Contracts

Phase 5 reuses these Phase 0 contracts:

- `AssetRef`
- `AlertRaised`
- `HealthSnapshot`
- `DiagnosticsCollect`
- `DiagnosticsResult`
- `RemediationExecute`
- `RemediationResult`
- `EvidenceBundle`

Host asset rules:

- Linux assets use `asset_kind = linux_host`.
- Windows assets use `asset_kind = windows_host`.

Host diagnostic profiles:

| Platform | Profiles |
| --- | --- |
| Linux | `host.summary`, `host.services`, `host.disk`, `host.logs` |
| Windows | `host.summary`, `host.services`, `host.event_logs`, `host.performance` |

Typed remediation actions allowed in v1:

| Action type | Linux | Windows |
| --- | --- | --- |
| `service.start` | yes | yes |
| `service.stop` | yes | yes |
| `service.restart` | yes | yes |

Typed remediation actions excluded in v1:

- arbitrary command execution
- arbitrary script execution
- package installation
- registry editing
- local user management

Integration boundaries:

| Platform | Event ingest | Polling | Diagnostics | Executor |
| --- | --- | --- | --- | --- |
| Linux | journald forwarding or syslog | host metrics and service status | constrained SSH and read-only system commands | constrained SSH and service-control commands |
| Windows | Windows Event Forwarding | WMI and performance counters | WinRM with constrained PowerShell queries | WinRM with constrained service-control commands |

## 6. Data Model And State Transitions

Host visibility rules:

- Host events and snapshots must still normalize into Phase 0 shared contracts.
- Host diagnostics results must still produce `EvidenceBundle`.
- Host remediations must still use the shared `RemediationExecute` and `RemediationResult` contracts.

Remediation history persistence:

- Every host remediation creates a timeline event in `portfolio_activity_events`.
- Every host remediation is linked to:
  - `incident_id`
  - `asset_id`
  - `approval_id`
  - executor action type
  - terminal `RemediationResult.outcome`

Host-specific post-execution evidence:

- After a host executor completes, the orchestrator must request one read-only verification diagnostics pass.
- The verification pass must produce a new `EvidenceBundle`.
- Incident state may move to `resolved` only after verification evidence is persisted or the operator explicitly closes the incident later.

## 7. Request/Data Flow

Linux flow:

1. Linux host events or polling data arrive through Linux-specific ingest or polling agents.
2. The data is normalized into shared contracts and flows through the same bridge and inventory path as network data.
3. Incident chat requests host diagnostics through `linux-diagnostics`.
4. If a typed remediation is approved, `linux-executor` performs the service action.
5. Post-execution verification diagnostics run automatically.

Windows flow:

1. Windows host events arrive through `windows-event-ingest` using WEF.
2. Polling data arrives through `windows-polling` using WMI and performance counters.
3. Incident chat requests host diagnostics through `windows-diagnostics`.
4. If a typed remediation is approved, `windows-executor` performs the service action.
5. Post-execution verification diagnostics run automatically.

## 8. Security And Authorization Rules

- Linux diagnostics and executors must use constrained SSH identities with only the minimum required permissions.
- Windows diagnostics and executors must use constrained WinRM identities with only the minimum required permissions.
- No host executor may accept free-form commands from `RemediationExecute.parameters`.
- Executors validate `action_type` against the v1 allowlist before doing any host work.
- Every host write action requires approval through Phase 4 semantics.
- Host-specific UI views must respect the same client-scoped auth model defined in Phase 1.

## 9. Acceptance Criteria

- Linux host visibility works end to end before Windows host visibility is enabled.
- Linux diagnostics work end to end before Linux host execution is enabled.
- Windows host visibility works end to end after Linux support is stable.
- Windows diagnostics work end to end before Windows host execution is enabled.
- All host remediation actions are approval-gated and typed.
- Host remediation history and post-execution evidence are visible in the incident cockpit and asset detail views.

## 10. Test Plan

- Linux parser and polling tests for normalized host alerts and snapshots.
- Windows parser and polling tests for normalized host alerts and snapshots.
- Integration tests for Linux diagnostics and executor actions.
- Integration tests for Windows diagnostics and executor actions.
- Approval integration tests for host remediations.
- Verification diagnostics tests after a successful host write action.
- UI integration tests for host asset detail, host diagnostics evidence, and remediation history.

## 11. Assumptions Locked By This Phase

- Linux ships before Windows.
- Host remediation is limited to typed service-control actions in v1.
- Post-remediation verification diagnostics are mandatory.
- Linux and Windows reuse the same orchestration and approval model established earlier.
