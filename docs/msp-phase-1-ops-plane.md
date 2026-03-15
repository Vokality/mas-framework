# MSP Phase 1: Ops Plane

This document defines the central human-facing ops plane. Shared contracts, ids, and state machines are owned by [Phase 0](./msp-phase-0-foundations.md).

## 1. Goal

Deliver a multi-user browser-based ops plane that can authenticate human operators, persist portfolio read models, expose REST and SSE APIs, and provide the UI shell for portfolio, client drill-in, incident cockpit, approvals, configuration, and chat.

## 2. In Scope

- `apps/mas-ops-api`
- `apps/mas-ops-ui`
- Local auth and session management
- Client-scoped human authorization
- Postgres schema groups for human-facing state
- REST route set for auth, clients, incidents, assets, chat, approvals, and config
- SSE endpoints and event envelope model
- UI route map and screen shells
- Session restore and stream reconnect behavior

## 3. Out Of Scope

- Device-specific ingest and polling behavior
- Fabric-local diagnostics execution
- Approval controller logic beyond the API contract
- Config deployment logic beyond the API contract
- Linux and Windows support
- SSO or OIDC

## 4. Package And App Changes

`apps/mas-ops-api` must be introduced with these modules:

| Module | Responsibility |
| --- | --- |
| `auth` | local login, logout, session restore, role checks |
| `db` | SQLAlchemy models, session management, Alembic migrations |
| `api` | FastAPI routers and response models |
| `projections` | portfolio and activity read-model writes |
| `streams` | SSE endpoints and replay handling |
| `connectors` | one fabric connector per client fabric |
| `chat` | chat session and message persistence |
| `config` | desired-state persistence and run tracking |

`apps/mas-ops-ui` must be introduced with these modules:

| Module | Responsibility |
| --- | --- |
| `routes` | route definitions and page composition |
| `api/generated` | generated OpenAPI client and TypeScript types |
| `auth` | session bootstrap, route guards, logout |
| `portfolio` | portfolio screen and client summary views |
| `incidents` | incident list and incident cockpit shell |
| `assets` | asset list and asset detail views |
| `approvals` | approvals inbox |
| `config` | config console |
| `chat` | global chat shell and incident chat shell |
| `streams` | EventSource consumers and event reducers |

The UI must consume only the generated OpenAPI client. Hand-authored duplicate request or response types are not allowed.

## 5. Public Interfaces And Contracts

Local auth model:

- Password hashes use Argon2id.
- Session storage is server-side in Postgres.
- Session cookies carry an opaque random token, not user data.
- Cookie name is `mas_ops_session`.
- Cookie policy is `HttpOnly`, `SameSite=Lax`, and `Secure` outside localhost development.
- Idle session timeout is 12 hours.
- Absolute session lifetime is 7 days.

Human roles:

| Role | Capabilities |
| --- | --- |
| `admin` | manage users, client access, config, approvals, portfolio, incidents, assets, and chat across all clients |
| `operator` | read authorized client data, use global and incident chat, approve or reject write actions, no config mutation |
| `viewer` | read authorized client data only |

Client-scoped authorization rules:

- `admin` is global and not client-scoped in v1.
- `operator` and `viewer` are scoped through explicit `client_id` grants.
- Every client-facing query, stream, chat request, approval action, and config access check must enforce client scope in `mas-ops-api`.
- UI route guards are advisory only. The API is authoritative.

HTTP route set:

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/auth/login` | create a session |
| `POST` | `/auth/logout` | destroy the current session |
| `GET` | `/auth/session` | restore the current session |
| `GET` | `/clients` | list authorized clients |
| `GET` | `/clients/{client_id}` | fetch one client summary |
| `GET` | `/clients/{client_id}/incidents` | list incidents for one client |
| `GET` | `/incidents/{incident_id}` | fetch one incident detail |
| `GET` | `/incidents/{incident_id}/activity` | fetch activity timeline entries for one incident |
| `GET` | `/clients/{client_id}/assets` | list assets for one client |
| `GET` | `/assets/{asset_id}` | fetch one asset detail |
| `POST` | `/chat/sessions` | create a global or incident-scoped chat session |
| `GET` | `/chat/sessions/{chat_session_id}` | fetch one chat session and its turns |
| `POST` | `/chat/sessions/{chat_session_id}/messages` | append one operator message |
| `GET` | `/approvals` | list approvals visible to the current user |
| `POST` | `/approvals/{approval_id}/decision` | submit an approval decision |
| `GET` | `/clients/{client_id}/config/desired-state` | fetch desired-state config |
| `PUT` | `/clients/{client_id}/config/desired-state` | replace desired-state config |
| `POST` | `/clients/{client_id}/config/validate` | start a validation run |
| `POST` | `/clients/{client_id}/config/apply` | start an apply run |

SSE endpoints:

| Route | Scope | Events |
| --- | --- | --- |
| `/streams/portfolio` | authorized portfolio view | `portfolio.updated`, `client.updated` |
| `/streams/clients/{client_id}` | one client | `client.updated`, `incident.updated`, `asset.updated`, `activity.appended` |
| `/streams/incidents/{incident_id}` | one incident | `incident.updated`, `activity.appended`, `approval.requested`, `approval.resolved` |
| `/streams/chat/{chat_session_id}` | one chat session | `chat.delta`, `chat.completed`, `approval.requested`, `approval.resolved` |

SSE envelope model:

- `id`: globally unique stream event id
- `event`: event type string
- `data`: JSON payload
- `retry`: reconnection hint in milliseconds

Each SSE event payload must include:

- `event_id`
- `client_id`
- `occurred_at`
- `subject_type`
- `subject_id`
- `payload`

UI route map:

| Route | Screen |
| --- | --- |
| `/login` | login screen |
| `/` | portfolio dashboard |
| `/clients/:clientId` | client detail view |
| `/incidents/:incidentId` | incident cockpit shell |
| `/approvals` | approvals inbox |
| `/clients/:clientId/config` | config console |
| `/chat` | global chat shell |

## 6. Data Model And State Transitions

Postgres schema groups:

| Schema group | Tables |
| --- | --- |
| users and sessions | `ops_users`, `ops_user_passwords`, `ops_sessions` |
| client access | `ops_user_client_access` |
| portfolio projections | `portfolio_clients`, `portfolio_incidents`, `portfolio_assets`, `portfolio_activity_events` |
| chat sessions and messages | `chat_sessions`, `chat_turns`, `chat_messages` |
| approvals | `approval_requests` |
| desired config | `config_desired_states` |
| config runs | `config_validation_runs`, `config_apply_runs` |

Required uniqueness constraints:

- `ops_users.email` unique
- `ops_user_client_access(user_id, client_id)` unique
- `portfolio_activity_events(client_id, fabric_id, source_event_id)` unique
- `chat_sessions.chat_session_id` unique
- `chat_turns(turn_id)` unique
- `approval_requests.approval_id` unique
- `config_desired_states(client_id)` unique
- `config_validation_runs.config_apply_run_id` unique
- `config_apply_runs.config_apply_run_id` unique

Session restore behavior:

- On application boot, the UI calls `GET /auth/session`.
- On `200`, the UI hydrates the current user, role, and client access.
- On `401`, the UI clears local state and redirects to `/login`.

Reconnect behavior:

- Every SSE stream must support `Last-Event-ID`.
- The API must replay committed events after the last acknowledged event id.
- Duplicate replay is not allowed.
- If the user loses scope to a client during a reconnect, the API returns `403` and the UI closes the stream.

## 7. Request/Data Flow

1. A human logs in to `mas-ops-ui`.
2. The UI restores the session through `GET /auth/session`.
3. The UI loads portfolio, client, incident, asset, approval, config, or chat data through REST.
4. The UI opens SSE streams for the currently visible scopes.
5. `mas-ops-api` serves data from Postgres read models and live connector updates.
6. Mutating user actions are posted to REST endpoints.
7. REST handlers persist the new state first and then dispatch connector or background work where required.

The UI never calls a client fabric directly.

## 8. Security And Authorization Rules

- Password verification happens only on the server.
- Session tokens are rotated on login and logout.
- Session cookies are invalidated server-side on logout.
- `viewer` cannot access mutating routes.
- `operator` cannot mutate desired-state configuration.
- `operator` may submit approval decisions only for authorized clients.
- `admin` may access all clients and all mutating routes.
- All SSE authorization checks must be re-evaluated when the stream is opened and when replay state is restored.
- `mas-ops-api` is the only component that may hold human session state.

## 9. Acceptance Criteria

- A human user can log in, restore a session, and log out.
- A human user sees only authorized clients.
- Portfolio, client, incident, asset, approval, config, and chat shells load from Postgres-backed APIs.
- SSE streams update the current view without polling.
- SSE reconnect resumes from `Last-Event-ID` without duplicate events.
- The UI route structure exists and is protected by auth and client scope.

## 10. Test Plan

- Auth tests for login success, login failure, logout, session restore, idle timeout, and absolute timeout.
- Authorization tests for `admin`, `operator`, and `viewer` across scoped and unscoped routes.
- API tests for every listed REST route.
- Database tests for uniqueness constraints and projection writes.
- SSE tests for event ordering, replay, reconnect, duplicate prevention, and scope enforcement.
- UI integration tests for login, portfolio load, client drill-in, incident shell load, approvals inbox load, config console load, and global chat shell load.

## 11. Assumptions Locked By This Phase

- Local auth is sufficient for v1.
- `operator` may approve writes but may not change desired-state configuration.
- `viewer` is strictly read-only.
- Postgres is the only source of truth for human-facing state.
- The incident cockpit route exists in Phase 1 even though incident interaction is completed in Phase 3.
