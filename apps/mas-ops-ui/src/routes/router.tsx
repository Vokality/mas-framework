import {
  createBrowserRouter,
  NavLink,
  Outlet,
  type RouteObject,
} from "react-router-dom";

import { ApprovalsPage } from "../approvals";
import { useAuth } from "../auth/AuthProvider";
import { RequireAuth } from "../auth/RequireAuth";
import { RequireClientAccess } from "../auth/RequireClientAccess";
import { ChatPage } from "../chat";
import { ConfigPage } from "../config";
import { IncidentPage } from "../incidents";
import { ClientPage, PortfolioPage } from "../portfolio";
import { LoginPage } from "./screens/LoginPage";

function ShellLayout() {
  const auth = useAuth();
  const primaryClientId = auth.session?.client_ids[0] ?? null;
  const visibleClientCount =
    auth.session?.role === "admin"
      ? "all"
      : String(auth.session?.client_ids.length ?? 0);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="eyebrow">Phase 2 Ops Plane</span>
          <h1>MAS Ops</h1>
          <p>
            Portfolio, client visibility, approvals, config, incident drill-in,
            and chat routes are bound to the ops-plane API contract.
          </p>
        </div>
        <nav className="nav">
          <NavLink to="/">Portfolio</NavLink>
          <NavLink to="/approvals">Approvals</NavLink>
          <NavLink to="/chat">Global Chat</NavLink>
          {primaryClientId ? (
            <>
              <NavLink to={`/clients/${primaryClientId}`}>Client Detail</NavLink>
              <NavLink to={`/clients/${primaryClientId}/config`}>
                Config Console
              </NavLink>
            </>
          ) : (
            <p className="nav-hint">
              Choose a client from the portfolio before drilling into detail routes.
            </p>
          )}
        </nav>
        <div className="grid">
          <div className="card">
            <h4>Session</h4>
            <p>{auth.session?.display_name ?? auth.session?.email}</p>
            <p className="muted-copy">
              Role: {auth.session?.role ?? "unknown"}
              <br />
              Authorized clients: {visibleClientCount}
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

export const opsRoutes: Array<RouteObject> = [
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/",
    element: (
      <RequireAuth>
        <ShellLayout />
      </RequireAuth>
    ),
    children: [
      {
        index: true,
        element: <PortfolioPage />,
      },
      {
        path: "clients/:clientId",
        element: (
          <RequireClientAccess>
            <ClientPage />
          </RequireClientAccess>
        ),
      },
      {
        path: "incidents/:incidentId",
        element: <IncidentPage />,
      },
      {
        path: "approvals",
        element: <ApprovalsPage />,
      },
      {
        path: "clients/:clientId/config",
        element: (
          <RequireClientAccess>
            <ConfigPage />
          </RequireClientAccess>
        ),
      },
      {
        path: "chat",
        element: <ChatPage />,
      },
    ],
  },
];

export const router = createBrowserRouter(opsRoutes);
