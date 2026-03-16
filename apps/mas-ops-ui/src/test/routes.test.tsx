import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  MockEventSource,
  installMockEventSource,
  installMockFetch,
  renderOpsUi,
} from "./testApp";

const CLIENT_ID = "11111111-1111-4111-8111-111111111111";
const FABRIC_ID = "33333333-3333-4333-8333-333333333333";
const INCIDENT_ID = "55555555-5555-4555-8555-555555555555";
const ASSET_ID = "77777777-7777-4777-8777-777777777777";

describe("MAS Ops UI Phase 1 routes", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    installMockEventSource();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.sessionStorage.clear();
  });

  test("login flow restores auth and lands on the portfolio view", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: { body: { detail: "unauthorized" }, status: 401 },
      },
      {
        method: "POST",
        path: "/auth/login",
        response: {
          body: {
            user_id: "user-1",
            email: "admin@example.com",
            display_name: "Admin User",
            role: "admin",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: "/clients",
        response: {
          body: [
            {
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              name: "Acme Corp",
              open_alert_count: 3,
              critical_asset_count: 1,
              updated_at: "2026-03-15T00:00:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: "/streams/portfolio?replay_only=true",
        response: { text: "" },
      },
    ]);

    renderOpsUi("/login");

    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "admin@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "password-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    expect(await screen.findByText("Client triage")).toBeInTheDocument();
    expect(await screen.findAllByText("Acme Corp")).toHaveLength(2);
    expect(
      await screen.findByRole("link", { name: "Open Priority Client" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Dense triage view")).toBeInTheDocument();
  });

  test("portfolio stream updates the current view without polling", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-2",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: "/clients",
        response: {
          body: [
            {
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              name: "Acme Corp",
              open_alert_count: 3,
              critical_asset_count: 1,
              updated_at: "2026-03-15T00:00:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: "/streams/portfolio?replay_only=true",
        response: { text: "" },
      },
    ]);

    renderOpsUi("/");
    expect(await screen.findAllByText("Acme Corp")).toHaveLength(2);

    const portfolioStream = MockEventSource.instances[0];
    await act(async () => {
      portfolioStream.emit(
        "client.updated",
        {
          event_id: "event-1",
          client_id: CLIENT_ID,
          occurred_at: "2026-03-15T00:01:00Z",
          subject_type: "portfolio_client",
          subject_id: CLIENT_ID,
          payload: {
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            name: "Acme Corp Updated",
            open_alert_count: 9,
            critical_asset_count: 2,
            updated_at: "2026-03-15T00:01:00Z",
          },
        },
        "101",
      );
    });

    expect(await screen.findAllByText("Acme Corp Updated")).toHaveLength(2);
  });

  test("client drill-in loads incidents and asset detail", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-3",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}`,
        response: {
          body: {
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            name: "Acme Corp",
            open_alert_count: 3,
            critical_asset_count: 1,
            updated_at: "2026-03-15T00:00:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/incidents`,
        response: {
          body: [
            {
              incident_id: INCIDENT_ID,
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              state: "investigating",
              severity: "major",
              summary: "Primary uplink is unstable",
              opened_at: "2026-03-15T00:00:00Z",
              updated_at: "2026-03-15T00:00:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/assets`,
        response: {
          body: [
            {
              asset_id: ASSET_ID,
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              asset_kind: "network_device",
              vendor: "Cisco",
              model: "Catalyst 9300",
              hostname: "edge-sw-01",
              mgmt_address: "10.0.0.10",
              site: "nyc-1",
              tags: ["core"],
              health_state: "degraded",
              health_observed_at: "2026-03-15T00:11:00Z",
              last_alert_at: "2026-03-15T00:05:00Z",
              updated_at: "2026-03-15T00:00:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/activity`,
        response: {
          body: [
            {
              activity_id: 3,
              source_event_id: "activity-3",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "network.snapshot.recorded",
              subject_type: "snapshot",
              subject_id: "snapshot-1",
              payload: {
                snapshot: {
                  asset: { asset_id: ASSET_ID, hostname: "edge-sw-01" },
                  health_state: "degraded",
                },
              },
              occurred_at: "2026-03-15T00:11:00Z",
            },
            {
              activity_id: 2,
              source_event_id: "activity-2",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "network.alert.raised",
              subject_type: "alert",
              subject_id: "alert-1",
              payload: {
                alert: {
                  asset: { asset_id: ASSET_ID, hostname: "edge-sw-01" },
                  severity: "major",
                  title: "Primary uplink changed state to down",
                },
              },
              occurred_at: "2026-03-15T00:05:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/assets/${ASSET_ID}`,
        response: {
          body: {
            asset_id: ASSET_ID,
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            asset_kind: "network_device",
            vendor: "Cisco",
            model: "Catalyst 9300",
            hostname: "edge-sw-01",
            mgmt_address: "10.0.0.10",
            site: "nyc-1",
            tags: ["core"],
            health_state: "degraded",
            health_observed_at: "2026-03-15T00:11:00Z",
            last_alert_at: "2026-03-15T00:05:00Z",
            updated_at: "2026-03-15T00:00:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/assets/${ASSET_ID}/activity`,
        response: {
          body: [
            {
              activity_id: 3,
              source_event_id: "activity-3",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "network.snapshot.recorded",
              subject_type: "snapshot",
              subject_id: "snapshot-1",
              payload: {
                snapshot: {
                  asset: { asset_id: ASSET_ID, hostname: "edge-sw-01" },
                  health_state: "degraded",
                },
              },
              occurred_at: "2026-03-15T00:11:00Z",
            },
            {
              activity_id: 2,
              source_event_id: "activity-2",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "network.alert.raised",
              subject_type: "alert",
              subject_id: "alert-1",
              payload: {
                alert: {
                  asset: { asset_id: ASSET_ID, hostname: "edge-sw-01" },
                  severity: "major",
                  title: "Primary uplink changed state to down",
                },
              },
              occurred_at: "2026-03-15T00:05:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/streams/clients/${CLIENT_ID}?replay_only=true`,
        response: { text: "" },
      },
    ]);

    renderOpsUi(`/clients/${CLIENT_ID}`);

    expect(await screen.findByText("Primary uplink is unstable")).toBeInTheDocument();
    expect(await screen.findByText(/Model: Catalyst 9300/)).toBeInTheDocument();
    expect(
      await screen.findAllByText("Primary uplink changed state to down"),
    ).toHaveLength(2);
    expect(await screen.findByText(/Last health: 2026-03-15 00:11:00 UTC/)).toBeInTheDocument();
  });

  test("client stream patches summary, activity, and asset detail without a full reload", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-3b",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}`,
        response: {
          body: {
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            name: "Acme Corp",
            open_alert_count: 3,
            critical_asset_count: 0,
            updated_at: "2026-03-15T00:00:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/incidents`,
        response: { body: [] },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/assets`,
        response: {
          body: [
            {
              asset_id: ASSET_ID,
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              asset_kind: "network_device",
              vendor: "Cisco",
              model: "Catalyst 9300",
              hostname: "edge-sw-01",
              mgmt_address: "10.0.0.10",
              site: "nyc-1",
              tags: ["core"],
              health_state: "degraded",
              health_observed_at: "2026-03-15T00:11:00Z",
              last_alert_at: "2026-03-15T00:05:00Z",
              updated_at: "2026-03-15T00:11:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/activity`,
        response: {
          body: [
            {
              activity_id: 3,
              source_event_id: "activity-3",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "network.snapshot.recorded",
              subject_type: "snapshot",
              subject_id: "snapshot-1",
              payload: {
                snapshot: {
                  asset: { asset_id: ASSET_ID, hostname: "edge-sw-01" },
                  health_state: "degraded",
                },
              },
              occurred_at: "2026-03-15T00:11:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/assets/${ASSET_ID}`,
        response: {
          body: {
            asset_id: ASSET_ID,
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            asset_kind: "network_device",
            vendor: "Cisco",
            model: "Catalyst 9300",
            hostname: "edge-sw-01",
            mgmt_address: "10.0.0.10",
            site: "nyc-1",
            tags: ["core"],
            health_state: "degraded",
            health_observed_at: "2026-03-15T00:11:00Z",
            last_alert_at: "2026-03-15T00:05:00Z",
            updated_at: "2026-03-15T00:11:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/assets/${ASSET_ID}/activity`,
        response: {
          body: [
            {
              activity_id: 3,
              source_event_id: "activity-3",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "network.snapshot.recorded",
              subject_type: "snapshot",
              subject_id: "snapshot-1",
              payload: {
                snapshot: {
                  asset: { asset_id: ASSET_ID, hostname: "edge-sw-01" },
                  health_state: "degraded",
                },
              },
              occurred_at: "2026-03-15T00:11:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/streams/clients/${CLIENT_ID}?replay_only=true`,
        response: { text: "" },
      },
    ]);

    renderOpsUi(`/clients/${CLIENT_ID}`);

    expect(await screen.findByText(/Alerts: 3 \| Critical assets: 0/)).toBeInTheDocument();
    expect(await screen.findByText(/Health: degraded/)).toBeInTheDocument();

    const clientStream = MockEventSource.instances[0];
    await act(async () => {
      clientStream.emit(
        "client.updated",
        {
          event_id: "event-21",
          client_id: CLIENT_ID,
          occurred_at: "2026-03-15T00:12:00Z",
          subject_type: "portfolio_client",
          subject_id: CLIENT_ID,
          payload: {
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            name: "Acme Corp",
            open_alert_count: 4,
            critical_asset_count: 1,
            updated_at: "2026-03-15T00:12:00Z",
          },
        },
        "201",
      );
      clientStream.emit(
        "activity.appended",
        {
          event_id: "event-22",
          client_id: CLIENT_ID,
          occurred_at: "2026-03-15T00:12:00Z",
          subject_type: "portfolio_activity",
          subject_id: "4",
          payload: {
            activity_id: 4,
            source_event_id: "activity-4",
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            incident_id: null,
            asset_id: ASSET_ID,
            event_type: "network.alert.raised",
            subject_type: "alert",
            subject_id: "alert-4",
            payload: {
              alert: {
                asset: { asset_id: ASSET_ID, hostname: "edge-sw-01" },
                severity: "critical",
                title: "Primary uplink changed state to down again",
              },
            },
            occurred_at: "2026-03-15T00:12:00Z",
          },
        },
        "202",
      );
      clientStream.emit(
        "asset.updated",
        {
          event_id: "event-23",
          client_id: CLIENT_ID,
          occurred_at: "2026-03-15T00:12:00Z",
          subject_type: "portfolio_asset",
          subject_id: ASSET_ID,
          payload: {
            asset_id: ASSET_ID,
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            asset_kind: "network_device",
            vendor: "Cisco",
            model: "Catalyst 9300",
            hostname: "edge-sw-01",
            mgmt_address: "10.0.0.10",
            site: "nyc-1",
            tags: ["core"],
            health_state: "critical",
            health_observed_at: "2026-03-15T00:12:00Z",
            last_alert_at: "2026-03-15T00:12:00Z",
            updated_at: "2026-03-15T00:12:00Z",
          },
        },
        "203",
      );
    });

    expect(await screen.findByText(/Alerts: 4 \| Critical assets: 1/)).toBeInTheDocument();
    expect(await screen.findByText(/Health: critical/)).toBeInTheDocument();
    expect(await screen.findByText(/Last alert: 2026-03-15 00:12:00 UTC/)).toBeInTheDocument();
    expect(
      await screen.findAllByText("Primary uplink changed state to down again"),
    ).toHaveLength(2);
  });

  test("client asset detail renders host service panels for Linux hosts", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-host-1",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}`,
        response: {
          body: {
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            name: "Acme Corp",
            open_alert_count: 1,
            critical_asset_count: 1,
            updated_at: "2026-03-15T00:00:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/incidents`,
        response: { body: [] },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/assets`,
        response: {
          body: [
            {
              asset_id: ASSET_ID,
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              asset_kind: "linux_host",
              vendor: "Linux",
              model: "Ubuntu 24.04",
              hostname: "web-01",
              mgmt_address: "10.20.0.15",
              site: "nyc-1",
              tags: ["linux", "production"],
              health_state: "critical",
              health_observed_at: "2026-03-15T00:11:00Z",
              last_alert_at: "2026-03-15T00:05:00Z",
              updated_at: "2026-03-15T00:11:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/activity`,
        response: {
          body: [
            {
              activity_id: 3,
              source_event_id: "activity-3",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "host.snapshot.recorded",
              subject_type: "snapshot",
              subject_id: "snapshot-1",
              payload: {
                snapshot: {
                  asset: { asset_id: ASSET_ID, hostname: "web-01" },
                  health_state: "critical",
                  metrics: {
                    cpu_percent: 96,
                    memory_percent: 72,
                    disk_percent: 88,
                    services: [
                      { service_name: "nginx", service_state: "failed" },
                      { service_name: "sshd", service_state: "running" },
                    ],
                  },
                },
              },
              occurred_at: "2026-03-15T00:11:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/assets/${ASSET_ID}`,
        response: {
          body: {
            asset_id: ASSET_ID,
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            asset_kind: "linux_host",
            vendor: "Linux",
            model: "Ubuntu 24.04",
            hostname: "web-01",
            mgmt_address: "10.20.0.15",
            site: "nyc-1",
            tags: ["linux", "production"],
            health_state: "critical",
            health_observed_at: "2026-03-15T00:11:00Z",
            last_alert_at: "2026-03-15T00:05:00Z",
            updated_at: "2026-03-15T00:11:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/assets/${ASSET_ID}/activity`,
        response: {
          body: [
            {
              activity_id: 3,
              source_event_id: "activity-3",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: null,
              asset_id: ASSET_ID,
              event_type: "host.snapshot.recorded",
              subject_type: "snapshot",
              subject_id: "snapshot-1",
              payload: {
                snapshot: {
                  asset: { asset_id: ASSET_ID, hostname: "web-01" },
                  health_state: "critical",
                  metrics: {
                    cpu_percent: 96,
                    memory_percent: 72,
                    disk_percent: 88,
                    services: [
                      { service_name: "nginx", service_state: "failed" },
                      { service_name: "sshd", service_state: "running" },
                    ],
                  },
                },
              },
              occurred_at: "2026-03-15T00:11:00Z",
            },
            {
              activity_id: 2,
              source_event_id: "activity-2",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: INCIDENT_ID,
              asset_id: ASSET_ID,
              event_type: "remediation.executed",
              subject_type: "incident",
              subject_id: INCIDENT_ID,
              payload: {
                post_state: {
                  service_name: "nginx",
                  service_state: "running",
                },
              },
              occurred_at: "2026-03-15T00:12:00Z",
            },
          ],
        },
      },
      {
        method: "GET",
        path: `/streams/clients/${CLIENT_ID}?replay_only=true`,
        response: { text: "" },
      },
    ]);

    renderOpsUi(`/clients/${CLIENT_ID}`);

    expect(await screen.findByText("Platform: Linux")).toBeInTheDocument();
    expect(await screen.findByText("Latest host metrics")).toBeInTheDocument();
    expect(await screen.findByText("96%")).toBeInTheDocument();
    expect(await screen.findByText("72%")).toBeInTheDocument();
    expect(await screen.findByText("88%")).toBeInTheDocument();
    expect(await screen.findByText("nginx")).toBeInTheDocument();
    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(await screen.findByText(/Latest remediation: nginx is running/)).toBeInTheDocument();
  });

  test("incident cockpit loads and exposes the incident chat shell", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-4",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: `/incidents/${INCIDENT_ID}`,
        response: {
          body: {
            incident_id: INCIDENT_ID,
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            state: "awaiting_approval",
            severity: "major",
            summary: "Primary uplink is unstable",
            recommended_actions: [
              {
                title: "Review the latest diagnostic evidence",
                details: "Use the newest evidence bundle before taking action.",
              },
            ],
            activity: [
              {
                activity_id: 1,
                source_event_id: "activity-1",
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                incident_id: INCIDENT_ID,
                event_type: "incident.updated",
                subject_type: "incident",
                subject_id: INCIDENT_ID,
                payload: { summary: "Investigation started" },
                occurred_at: "2026-03-15T00:00:00Z",
              },
            ],
            approvals: [
              {
                approval_id: "approval-incident-1",
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                incident_id: INCIDENT_ID,
                state: "pending",
                action_kind: "network.remediation",
                title: "Bounce primary uplink",
                requested_at: "2026-03-15T00:02:00Z",
                expires_at: "2026-03-15T01:02:00Z",
                requested_by_agent: "core-orchestrator",
                payload: { action_scope: "incident_remediation" },
                risk_summary: "May briefly impact the primary uplink.",
                decided_by_user_id: null,
                decision_reason: null,
                decided_at: null,
                approved_at: null,
                rejected_at: null,
                expired_at: null,
                cancelled_at: null,
                executed_at: null,
              },
            ],
            assets: [
              {
                asset_id: ASSET_ID,
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                asset_kind: "network_device",
                vendor: "Cisco",
                model: "Catalyst 9300",
                hostname: "edge-sw-01",
                mgmt_address: "10.0.0.10",
                site: "nyc-1",
                tags: ["core"],
                health_state: "degraded",
                health_observed_at: "2026-03-15T00:00:00Z",
                last_alert_at: "2026-03-15T00:00:00Z",
                updated_at: "2026-03-15T00:00:00Z",
              },
            ],
            evidence_bundles: [
              {
                evidence_bundle_id: "evidence-1",
                incident_id: INCIDENT_ID,
                asset_id: ASSET_ID,
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                collected_at: "2026-03-15T00:01:00Z",
                summary: "Diagnostics confirmed the primary uplink is down.",
                items: [],
              },
            ],
            opened_at: "2026-03-15T00:00:00Z",
            updated_at: "2026-03-15T00:00:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/streams/incidents/${INCIDENT_ID}?replay_only=true`,
        response: { text: "" },
      },
    ]);

    renderOpsUi(`/incidents/${INCIDENT_ID}`);

    expect(await screen.findByText("Primary uplink is unstable")).toBeInTheDocument();
    expect(await screen.findByText("Diagnostics confirmed the primary uplink is down.")).toBeInTheDocument();
    expect(await screen.findByText("Review the latest diagnostic evidence")).toBeInTheDocument();
    expect(await screen.findByText(/Bounce primary uplink/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Create Session" })).toBeInTheDocument();
  });

  test("incident cockpit renders host remediation verification details", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-host-2",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: `/incidents/${INCIDENT_ID}`,
        response: {
          body: {
            incident_id: INCIDENT_ID,
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            state: "resolved",
            severity: "major",
            summary: "Linux diagnostics captured host.services evidence for web-01.",
            recommended_actions: [
              {
                title: "Continue post-remediation monitoring",
                details: "Verification evidence was captured after the approved host action.",
              },
            ],
            activity: [
              {
                activity_id: 1,
                source_event_id: "activity-1",
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                incident_id: INCIDENT_ID,
                event_type: "remediation.executed",
                subject_type: "incident",
                subject_id: INCIDENT_ID,
                payload: {
                  post_state: {
                    service_name: "nginx",
                    service_state: "running",
                  },
                },
                occurred_at: "2026-03-15T00:12:00Z",
              },
              {
                activity_id: 2,
                source_event_id: "activity-2",
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                incident_id: INCIDENT_ID,
                event_type: "remediation.verified",
                subject_type: "incident",
                subject_id: INCIDENT_ID,
                payload: {
                  post_state: {
                    service_name: "nginx",
                    service_state: "running",
                  },
                },
                occurred_at: "2026-03-15T00:13:00Z",
              },
            ],
            approvals: [],
            assets: [
              {
                asset_id: ASSET_ID,
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                asset_kind: "linux_host",
                vendor: "Linux",
                model: "Ubuntu 24.04",
                hostname: "web-01",
                mgmt_address: "10.20.0.15",
                site: "nyc-1",
                tags: ["linux", "production"],
                health_state: "critical",
                health_observed_at: "2026-03-15T00:00:00Z",
                last_alert_at: "2026-03-15T00:00:00Z",
                updated_at: "2026-03-15T00:00:00Z",
              },
            ],
            evidence_bundles: [
              {
                evidence_bundle_id: "evidence-1",
                incident_id: INCIDENT_ID,
                asset_id: ASSET_ID,
                client_id: CLIENT_ID,
                fabric_id: FABRIC_ID,
                collected_at: "2026-03-15T00:14:00Z",
                summary: "Linux diagnostics captured host.services evidence for web-01.",
                items: [
                  {
                    kind: "host_services",
                    services: [
                      { service_name: "nginx", service_state: "running" },
                      { service_name: "sshd", service_state: "running" },
                    ],
                  },
                ],
              },
            ],
            opened_at: "2026-03-15T00:00:00Z",
            updated_at: "2026-03-15T00:14:00Z",
          },
        },
      },
      {
        method: "GET",
        path: `/streams/incidents/${INCIDENT_ID}?replay_only=true`,
        response: { text: "" },
      },
    ]);

    renderOpsUi(`/incidents/${INCIDENT_ID}`);

    const timelineHeading = await screen.findByText("Host Remediation Timeline");
    const timelineCard = timelineHeading.closest("article");
    expect(timelineCard).not.toBeNull();
    expect(await screen.findByText("nginx: running | sshd: running")).toBeInTheDocument();
    expect(within(timelineCard as HTMLElement).getByText("remediation.executed")).toBeInTheDocument();
  });

  test("approvals inbox loads visible approvals", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-5",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
      {
        method: "GET",
        path: "/approvals",
        response: {
          body: [
            {
              approval_id: "approval-1",
              client_id: CLIENT_ID,
              fabric_id: FABRIC_ID,
              incident_id: INCIDENT_ID,
              state: "pending",
              action_kind: "network.remediation",
              title: "Bounce primary uplink",
              requested_at: "2026-03-15T00:00:00Z",
              expires_at: "2026-03-15T01:00:00Z",
              requested_by_agent: "core-orchestrator",
              payload: { action_type: "interface.shutdown_no_shutdown" },
              risk_summary: "May briefly impact the primary uplink.",
              decided_by_user_id: null,
              decision_reason: null,
              decided_at: null,
              approved_at: null,
              rejected_at: null,
              expired_at: null,
              cancelled_at: null,
              executed_at: null,
            },
          ],
        },
      },
    ]);

    renderOpsUi("/approvals");

    expect(await screen.findByText("Bounce primary uplink")).toBeInTheDocument();
  });

  test("config console loads desired-state data", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-6",
            email: "admin@example.com",
            display_name: "Admin User",
            role: "admin",
            client_ids: [],
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/config/desired-state`,
        response: {
          body: {
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            desired_state_version: 2,
            tenant_metadata: { display_name: "Acme Corp" },
            policy: { default_mode: "deny" },
            inventory_sources: [{ kind: "snmp" }],
            notification_routes: [{ kind: "email" }],
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/config/runs`,
        response: {
          body: {
            validation_runs: [
              {
                config_apply_run_id: "validation-1",
                client_id: CLIENT_ID,
                desired_state_version: 2,
                status: "valid",
                errors: [],
                warnings: [],
                requested_by_user_id: "user-6",
                requested_at: "2026-03-15T00:01:00Z",
                validated_at: "2026-03-15T00:01:05Z",
              },
            ],
            apply_runs: [
              {
                config_apply_run_id: "apply-1",
                client_id: CLIENT_ID,
                desired_state_version: 2,
                status: "pending",
                approval_id: "approval-config-1",
                requested_by_user_id: "user-6",
                requested_at: "2026-03-15T00:02:00Z",
                started_at: null,
                completed_at: null,
                error_summary: null,
                steps: [],
              },
            ],
          },
        },
      },
      {
        method: "GET",
        path: `/streams/clients/${CLIENT_ID}?replay_only=true`,
        response: { text: "" },
      },
    ]);

    renderOpsUi(`/clients/${CLIENT_ID}/config`);

    expect(await screen.findByText(/Version 2/)).toBeInTheDocument();
    expect(await screen.findByText(/Apply: pending/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Save Desired State" })).toBeInTheDocument();
  });

  test("config console cancels a pending apply run", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-6",
            email: "admin@example.com",
            display_name: "Admin User",
            role: "admin",
            client_ids: [],
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/config/desired-state`,
        response: {
          body: {
            client_id: CLIENT_ID,
            fabric_id: FABRIC_ID,
            desired_state_version: 2,
            tenant_metadata: { display_name: "Acme Corp" },
            policy: { default_mode: "deny" },
            inventory_sources: [{ kind: "snmp" }],
            notification_routes: [{ kind: "email" }],
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/config/runs`,
        response: {
          body: {
            validation_runs: [],
            apply_runs: [
              {
                config_apply_run_id: "apply-1",
                client_id: CLIENT_ID,
                desired_state_version: 2,
                status: "pending",
                approval_id: "approval-config-1",
                requested_by_user_id: "user-6",
                requested_at: "2026-03-15T00:02:00Z",
                started_at: null,
                completed_at: null,
                error_summary: null,
                steps: [],
              },
            ],
          },
        },
      },
      {
        method: "GET",
        path: `/streams/clients/${CLIENT_ID}?replay_only=true`,
        response: { text: "" },
      },
      {
        method: "POST",
        path: `/clients/${CLIENT_ID}/config/runs/apply-1/cancel`,
        response: {
          body: {
            config_apply_run_id: "apply-1",
            client_id: CLIENT_ID,
            desired_state_version: 2,
            status: "cancelled",
            started_at: null,
            completed_at: "2026-03-15T00:03:00Z",
            error_summary: "Cancelled from config console",
          },
        },
      },
      {
        method: "GET",
        path: `/clients/${CLIENT_ID}/config/runs`,
        response: {
          body: {
            validation_runs: [],
            apply_runs: [
              {
                config_apply_run_id: "apply-1",
                client_id: CLIENT_ID,
                desired_state_version: 2,
                status: "cancelled",
                approval_id: "approval-config-1",
                requested_by_user_id: "user-6",
                requested_at: "2026-03-15T00:02:00Z",
                started_at: null,
                completed_at: "2026-03-15T00:03:00Z",
                error_summary: "Cancelled from config console",
                steps: [],
              },
            ],
          },
        },
      },
    ]);

    renderOpsUi(`/clients/${CLIENT_ID}/config`);

    expect(await screen.findByText(/Apply: pending/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel Pending Apply" }));

    expect(await screen.findByText(/Apply: cancelled/)).toBeInTheDocument();
  });

  test("global chat shell loads", async () => {
    installMockFetch([
      {
        method: "GET",
        path: "/auth/session",
        response: {
          body: {
            user_id: "user-7",
            email: "operator@example.com",
            display_name: "Operator User",
            role: "operator",
            client_ids: [CLIENT_ID],
          },
        },
      },
    ]);

    renderOpsUi("/chat");

    expect(await screen.findByText("Read-only portfolio summary")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Create Session" })).toBeInTheDocument();
  });
});
