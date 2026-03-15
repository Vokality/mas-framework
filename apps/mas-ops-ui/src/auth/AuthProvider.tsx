import {
  createContext,
  useContext,
  useEffect,
  useState,
  type PropsWithChildren,
} from "react";

import { ApiError, masOpsApiClient, type SessionResponse } from "../api/client";

type AuthStatus = "loading" | "anonymous" | "authenticated";

type AuthState = {
  status: AuthStatus;
  isAuthenticated: boolean;
  session: SessionResponse | null;
  bootstrapError: string | null;
  login(email: string, password: string): Promise<void>;
  logout(): Promise<void>;
  refreshSession(): Promise<void>;
  canAccessClient(clientId: string): boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      try {
        const currentSession = await masOpsApiClient.restoreSession();
        if (!active) {
          return;
        }
        setSession(currentSession);
        setStatus("authenticated");
        setBootstrapError(null);
      } catch (error) {
        if (!active) {
          return;
        }
        if (error instanceof ApiError && error.status === 401) {
          setSession(null);
          setStatus("anonymous");
          setBootstrapError(null);
          return;
        }
        console.error("Failed to restore MAS Ops session", error);
        setSession(null);
        setStatus("anonymous");
        setBootstrapError("The ops API session could not be restored.");
      }
    }

    void bootstrap();
    return () => {
      active = false;
    };
  }, []);

  const value: AuthState = {
    status,
    isAuthenticated: status === "authenticated",
    session,
    bootstrapError,
    async login(email: string, password: string) {
      const authenticatedSession = await masOpsApiClient.login({ email, password });
      setSession(authenticatedSession);
      setStatus("authenticated");
      setBootstrapError(null);
    },
    async logout() {
      try {
        await masOpsApiClient.logout();
      } finally {
        setSession(null);
        setStatus("anonymous");
      }
    },
    async refreshSession() {
      const currentSession = await masOpsApiClient.restoreSession();
      setSession(currentSession);
      setStatus("authenticated");
      setBootstrapError(null);
    },
    canAccessClient(clientId: string) {
      if (session === null) {
        return false;
      }
      if (session.role === "admin") {
        return true;
      }
      return session.client_ids.includes(clientId);
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return value;
}
