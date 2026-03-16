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
        <header className="sidebar-brand">
          <span className="eyebrow">MSP Command Center</span>
          <h1>MAS Ops</h1>
          <p>
            Start in the portfolio queue, open the client that needs attention,
            then work inside the incident cockpit. Approvals and config are support
            tools, not the starting point.
          </p>
        </header>
        <section className="sidebar-section">
          <details className="sidebar-collapse" open>
            <summary className="sidebar-label">Workflow</summary>
            <nav className="nav">
              <NavLink
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
                to="/"
              >
                <strong>Portfolio Queue</strong>
                <small>See which clients need action right now.</small>
              </NavLink>
              <NavLink
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
                to="/approvals"
              >
                <strong>Approval Inbox</strong>
                <small>Review pending write actions and deferred runs.</small>
              </NavLink>
              <NavLink
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
                to="/chat"
              >
                <strong>Portfolio Chat</strong>
                <small>Optional, read-only summary across visible clients.</small>
              </NavLink>
              {preferredClientId ? (
                <>
                  <NavLink
                    className={({ isActive }) =>
                      `nav-item${isActive ? " active" : ""}`
                    }
                    to={`/clients/${preferredClientId}`}
                  >
                    <strong>Last Client Workspace</strong>
                    <small>
                      Jump back into the client you opened most recently.
                    </small>
                  </NavLink>
                  <NavLink
                    className={({ isActive }) =>
                      `nav-item${isActive ? " active" : ""}`
                    }
                    to={`/clients/${preferredClientId}/config`}
                  >
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
          </details>
        </section>
        <section className="sidebar-section sidebar-playbook">
          <details className="sidebar-collapse" open>
            <summary className="sidebar-label">Operator loop</summary>
            <ol className="playbook-list">
              <li>Find the loudest client in the portfolio queue.</li>
              <li>Open the client workspace and select the active incident.</li>
              <li>
                Use incident chat, evidence, and approvals to decide the next move.
              </li>
            </ol>
          </details>
        </section>
        <section className="sidebar-section">
          <details className="sidebar-collapse" open>
            <summary className="sidebar-label">Session</summary>
            <div className="card sidebar-card">
              <h4>Session</h4>
              <p>{auth.session?.display_name ?? auth.session?.email}</p>
              <p className="muted-copy">
                Role: {auth.session?.role ?? "unknown"}
                <br />
                Scope: {visibleClientCount}
              </p>
              <button
                className="sidebar-signout"
                onClick={() => {
                  void auth.logout();
                }}
              >
                Sign Out
              </button>
            </div>
          </details>
        </section>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
