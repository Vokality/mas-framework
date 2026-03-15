# MSP Phase 2: Network Visibility

This document defines the first fabric-to-ops visibility slice. Shared contracts, ids, and state machines are owned by [Phase 0](./msp-phase-0-foundations.md). Human-facing API and UI shell behavior are owned by [Phase 1](./msp-phase-1-ops-plane.md).

## 1. Goal

Deliver read-only Cisco and FortiGate visibility from client fabrics into the central ops plane through deterministic edge agents, inventory state, and normalized portfolio projections.

## 2. In Scope

- `OpsBridgeAgent`
- `InventoryAgent`
- `NetworkEventIngestAgent`
- `NetworkPollingAgent`
- Cisco and FortiGate support only
- Syslog ingest
- SNMP trap ingest
- SNMPv3 polling
- Visibility projections into the ops plane
- Read-only UI views backed by those projections

## 3. Out Of Scope

- Fabric-local incident reasoning
- Fabric-local diagnostics execution
- Write actions or approvals
- Linux support
- Windows support
- Arbitrary third-party network vendors beyond Cisco and FortiGate

## 4. Package And App Changes

`packages/mas-msp-core` changes:

| Component | Responsibility |
| --- | --- |
| `InventoryAgent` | persist `AssetRef` records, bind alerts and snapshots to assets, maintain current asset health summary |
| `OpsBridgeAgent` | accept normalized visibility messages and emit `PortfolioEvent` records to the ops-plane connector |

`packages/mas-msp-network` changes:

| Component | Responsibility |
| --- | --- |
| `NetworkEventIngestAgent` | receive syslog and SNMP traps, normalize them into `AlertRaised` |
| `NetworkPollingAgent` | poll supported devices over SNMPv3 and normalize results into `HealthSnapshot` |
| `vendors/cisco` | Cisco-specific syslog, trap, and poll normalization |
| `vendors/fortigate` | FortiGate-specific syslog, trap, and poll normalization |

`apps/mas-ops-api` changes:

| Component | Responsibility |
| --- | --- |
| `connectors/fabric_connector` | authenticate into each client fabric as `ops-plane-{client_id}` |
| `projections/portfolio_ingest` | consume `PortfolioEvent` records and update Postgres read models |

`apps/mas-ops-ui` changes:

| Screen area | Responsibility |
| --- | --- |
| portfolio dashboard | show client health summary and alert counts |
| client detail | show asset inventory, latest alerts, and latest health state |
| asset detail | show normalized asset identity and last known health |

## 5. Public Interfaces And Contracts

Phase 2 consumes and emits these Phase 0 contracts:

- `AssetRef`
- `AlertRaised`
- `HealthSnapshot`
- `PortfolioEvent`

MAS message types introduced in this phase:

| Message type | Sender | Receiver | Payload |
| --- | --- | --- | --- |
| `alert.raised` | `network-event-ingest` | `inventory` | `AlertRaised` |
| `health.snapshot` | `network-polling` | `inventory` | `HealthSnapshot` |
| `portfolio.publish` | `inventory` | `ops-bridge` | `AlertRaised` or `HealthSnapshot` plus resolved `AssetRef` |
| `portfolio.event` | `ops-bridge` | `ops-plane-{client_id}` | `PortfolioEvent` |

Phase 2 `PortfolioEvent.event_type` values:

- `asset.upserted`
- `asset.health.changed`
- `network.alert.raised`
- `network.snapshot.recorded`

Supported input surfaces:

| Input | Agent | Supported vendors |
| --- | --- | --- |
| syslog | `network-event-ingest` | Cisco, FortiGate |
| SNMP traps | `network-event-ingest` | Cisco, FortiGate |
| SNMPv3 polling | `network-polling` | Cisco, FortiGate |

Normalization rules:

- Alert severity must map into the shared `severity` enum.
- Syslog and trap inputs must set `source_kind` to `syslog` or `snmp_trap`.
- Polling outputs must set `source_kind` to `snmp_poll`.
- Asset binding priority is:
  1. explicit inventory match on `mgmt_address`
  2. vendor serial match if present
  3. hostname match within the same client
  4. create a new `AssetRef`
- Unsupported vendor-specific fields may be preserved only inside `normalized_facts`, never as raw vendor payloads.

## 6. Data Model And State Transitions

Asset projection rules:

- `InventoryAgent` is the authoritative source of `AssetRef`.
- One `asset_id` maps to one client-local managed asset.
- `health_state` on the asset record is replaced whenever a newer `HealthSnapshot.collected_at` arrives.
- Alert records do not change asset identity.

Portfolio projection tables affected in this phase:

- `portfolio_clients`
- `portfolio_assets`
- `portfolio_activity_events`

Client summary projection rules:

- `portfolio_clients.open_alert_count` increments on `network.alert.raised` and decrements only when later phases resolve incidents.
- `portfolio_clients.critical_asset_count` is derived from the latest projected asset health states.

Activity projection rules:

- Every ingested `PortfolioEvent` becomes one `portfolio_activity_events` row.
- Projection writes are idempotent on `(client_id, fabric_id, source_event_id)`.

## 7. Request/Data Flow

1. A Cisco or FortiGate device emits syslog or a trap, or responds to an SNMPv3 poll.
2. The appropriate network agent normalizes the input into `AlertRaised` or `HealthSnapshot`.
3. `InventoryAgent` binds the payload to an `AssetRef` and updates asset state if required.
4. `InventoryAgent` sends `portfolio.publish` to `OpsBridgeAgent`.
5. `OpsBridgeAgent` converts the normalized payload into a `PortfolioEvent`.
6. `ops-plane-{client_id}` receives `portfolio.event` from `OpsBridgeAgent`.
7. `mas-ops-api` writes the event into Postgres projection tables.
8. `mas-ops-ui` reads and streams the updated client and asset views.

Phase 2 is strictly outbound from the fabric to the ops plane. The ops plane does not issue diagnostics, write actions, or chat commands in this phase.

## 8. Security And Authorization Rules

- `ops-plane-{client_id}` is read-only in Phase 2.
- `OpsBridgeAgent` may send `portfolio.event` messages only to the matching `ops-plane-{client_id}` connector.
- `network-event-ingest` and `network-polling` may not talk to the ops plane directly.
- Only normalized contracts cross from the fabric into the ops plane.
- Phase 2 does not enable any write path from the ops plane into client fabrics.

## 9. Acceptance Criteria

- Cisco and FortiGate syslog messages appear as normalized alerts in the ops plane.
- Cisco and FortiGate SNMP traps appear as normalized alerts in the ops plane.
- SNMPv3 polling results appear as normalized health snapshots in the ops plane.
- Assets are created or updated deterministically in inventory.
- Portfolio and client views display projected network visibility data without direct fabric access from the browser.
- Phase 2 can be implemented without introducing `core-orchestrator` or diagnostics agents.

## 10. Test Plan

- Parser tests for Cisco and FortiGate syslog normalization.
- Parser tests for Cisco and FortiGate SNMP trap normalization.
- Polling tests for SNMPv3 normalization into `HealthSnapshot`.
- Inventory tests for asset binding precedence and stable `asset_id` behavior.
- Bridge tests for `PortfolioEvent` creation and routing to `ops-plane-{client_id}`.
- Projection tests for idempotent writes and client summary updates.
- UI integration tests for portfolio, client detail, asset list, and asset detail views with projected network data.

## 11. Assumptions Locked By This Phase

- Phase 2 supports Cisco and FortiGate only.
- Phase 2 is read-only.
- The ops plane connector remains read-only until later phases.
- Asset binding uses address, serial, then hostname precedence.
- Incident creation is deferred until Phase 3.
