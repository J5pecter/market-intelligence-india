"use client";

import { ReactNode, useEffect, useState } from "react";
import { Loading, Notice, Panel } from "@/components/primitives";
import { api, setToken } from "@/lib/api";

export interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  notifications: Record<string, boolean>;
}

/**
 * Gates the personal sections (watchlist, alerts, portfolio, admin).
 *
 * Registration is open by default and the *first* account created becomes the
 * administrator - which is why the sign-in panel says so out loud rather than
 * shipping a default password.
 */
export function AuthGate({
  children,
  requireRole,
}: {
  children: (user: CurrentUser) => ReactNode;
  requireRole?: "ADMIN" | "ANALYST";
}) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [checking, setChecking] = useState(true);

  const load = async () => {
    setChecking(true);
    const response = await api.get<CurrentUser>("/api/auth/me");
    setUser(response.data ?? null);
    setChecking(false);
  };

  useEffect(() => {
    load();
  }, []);

  if (checking) return <Panel><Loading label="Checking your session" /></Panel>;
  if (!user) return <AuthForm onAuthenticated={load} />;

  const rank: Record<string, number> = { USER: 0, ANALYST: 1, ADMIN: 2 };
  if (requireRole && rank[user.role] < rank[requireRole]) {
    return (
      <Panel title="Not permitted">
        <Notice tone="warn">
          This section needs the {requireRole} role. You are signed in as{" "}
          {user.email} ({user.role}).
        </Notice>
      </Panel>
    );
  }

  return <>{children(user)}</>;
}

function AuthForm({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await api.post<{ access_token: string }>(
      mode === "login" ? "/api/auth/login" : "/api/auth/register",
      mode === "login"
        ? { email, password }
        : { email, password, display_name: displayName },
      false,
    );
    setBusy(false);
    if (response.error) {
      setError(response.error);
      return;
    }
    setToken(response.data!.access_token);
    onAuthenticated();
  };

  return (
    <Panel title={mode === "login" ? "Sign in" : "Create an account"}>
      <form onSubmit={submit} className="max-w-sm space-y-2.5">
        {mode === "register" && (
          <label className="block">
            <span className="text-2xs text-ink-muted">Display name</span>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="field mt-0.5"
              autoComplete="name"
            />
          </label>
        )}
        <label className="block">
          <span className="text-2xs text-ink-muted">Email</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="field mt-0.5"
            autoComplete="email"
          />
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">
            Password {mode === "register" && "(minimum 10 characters)"}
          </span>
          <input
            type="password"
            required
            minLength={mode === "register" ? 10 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="field mt-0.5"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </label>

        {error && <Notice tone="neg">{error}</Notice>}

        <div className="flex items-center gap-2">
          <button className="btn btn-accent" type="submit" disabled={busy}>
            {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
          <button
            type="button"
            className="text-2xs text-accent underline underline-offset-2"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login" ? "Create an account" : "I already have an account"}
          </button>
        </div>

        {mode === "register" && (
          <Notice tone="info">
            The first account created on a fresh deployment is granted the
            ADMIN role automatically. There is no default password shipped with
            this project.
          </Notice>
        )}
      </form>
    </Panel>
  );
}
