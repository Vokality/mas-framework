import { createBrowserRouter, type RouteObject } from "react-router-dom";

import { ApprovalsPage } from "../approvals";
import { RequireAuth } from "../auth/RequireAuth";
import { RequireClientAccess } from "../auth/RequireClientAccess";
import { ChatPage } from "../chat";
import { ConfigPage } from "../config";
import { IncidentPage } from "../incidents";
import { ClientPage, PortfolioPage } from "../portfolio";
import { ShellLayout } from "./ShellLayout";
import { LoginPage } from "./screens/LoginPage";

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
