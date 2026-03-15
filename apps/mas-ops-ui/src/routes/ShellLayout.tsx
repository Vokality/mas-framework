import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";
import { readLastViewedClientId } from "../portfolio/clientSelection";

export function ShellLayout() {
  const auth = useAuth();
  const location = useLocation();
  const [rememberedClientId, setRememberedClientId] = useState<string | null>(() =>
    readLastViewedClientId(),
  );

  useEffect(() => {
    setRememberedClientId(readLastViewedClientId());
  }, [location.pathname]);

  const preferredClientId = auth.session?.client_ids[0] ?? rememberedClientId;
  const visibleClientCount =
    auth.session?.role === "admin"
      ? "all clients"
      : `${auth.session?.client_ids.length ?? 0} authorized clients`;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="eyebrow">MSP Command Center</span>
          <h1>MAS Ops</h1>
          <p>
            Start in the portfolio queue, open the client that needs attention,
            then work inside the incident cockpit. Approvals and config are support
            tools, not the starting point.
          </p>
        </div>
        <div className="sidebar-section">
          <span className="sidebar-label">Workflow</span>
          <nav className="nav">
            <NavLink to="/">
              <strong>Portfolio Queue</strong>
              <small>See which clients need action right now.</small>
            </NavLink>
            <NavLink to="/approvals">
              <strong>Approval Inbox</strong>
              <small>Review pending write actions and deferred runs.</small>
            </NavLink>
            <NavLink to="/chat">
              <strong>Portfolio Chat</strong>
              <small>Optional, read-only summary across visible clients.</small>
            </NavLink>
            {preferredClientId ? (
              <>
                <NavLink to={`/clients/${preferredClientId}`}>
                  <strong>Last Client Workspace</strong>
                  <small>Jump back into the client you opened most recently.</small>
                </NavLink>
                <NavLink to={`/clients/${preferredClientId}/config`}>
                  <strong>Last Client Config</strong>
                  <small>Admin-only desired-state and apply history.</small>
                </NavLink>
              </>
            ) : (
              <p className="nav-hint">
                Open any client from the portfolio queue to pin it here for quick
                return navigation.
              </p>
            )}
          </nav>
        </div>
        <div className="sidebar-section sidebar-playbook">
          <span className="sidebar-label">Operator Loop</span>
          <ol className="playbook-list">
            <li>Find the loudest client in the portfolio queue.</li>
            <li>Open the client workspace and select the active incident.</li>
            <li>Use incident chat, evidence, and approvals to decide the next move.</li>
          </ol>
        </div>
        <div className="grid">
          <div className="card sidebar-card">
            <h4>Session</h4>
            <p>{auth.session?.display_name ?? auth.session?.email}</p>
            <p className="muted-copy">
              Role: {auth.session?.role ?? "unknown"}
              <br />
              Scope: {visibleClientCount}
            </p>
            <button
              onClick={() => {
                void auth.logout();
              }}
            >
              Sign Out
            </button>
          </div>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
