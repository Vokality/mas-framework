import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";

type LoginLocationState = {
  from?: string;
};

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (auth.status === "loading") {
    return (
      <div className="login">
        <div className="login-panel">
          <span className="eyebrow">Session Restore</span>
          <h2>Checking current session</h2>
          <p className="hero-subtitle">The ops plane is restoring any existing server-side session.</p>
        </div>
      </div>
    );
  }

  if (auth.isAuthenticated) {
    return <Navigate replace to="/" />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      await auth.login(email, password);
      const from = (location.state as LoginLocationState | null)?.from;
      navigate(from && from.length > 0 ? from : "/", { replace: true });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setErrorMessage("Invalid email or password.");
      } else {
        console.error("Failed to log in to MAS Ops", error);
        setErrorMessage("The ops API could not complete the login request.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="login">
      <div className="login-panel">
        <span className="eyebrow">Local Auth</span>
        <h2>MAS Ops Console</h2>
        <p className="hero-subtitle">
          Sign in to the MSP operator console. The normal flow is portfolio queue,
          then client workspace, then incident cockpit.
        </p>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>Email</span>
            <input
              autoComplete="email"
              name="email"
              onChange={(event) => {
                setEmail(event.target.value);
              }}
              type="email"
              value={email}
            />
          </label>
          <label>
            <span>Password</span>
            <input
              autoComplete="current-password"
              name="password"
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              type="password"
              value={password}
            />
          </label>
          {errorMessage ? <p className="form-error">{errorMessage}</p> : null}
          {auth.bootstrapError ? (
            <p className="form-error">{auth.bootstrapError}</p>
          ) : null}
          <button disabled={isSubmitting} type="submit">
            {isSubmitting ? "Signing In..." : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}
