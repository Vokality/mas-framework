import type { ReactNode } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";

import { useAuth } from "./AuthProvider";

type RequireClientAccessProps = {
  children: ReactNode;
};

export function RequireClientAccess({
  children,
}: RequireClientAccessProps) {
  const auth = useAuth();
  const location = useLocation();
  const { clientId } = useParams();

  if (auth.status === "loading") {
    return children;
  }
  if (!clientId || !auth.canAccessClient(clientId)) {
    return <Navigate replace to="/" state={{ from: location.pathname }} />;
  }
  return children;
}
