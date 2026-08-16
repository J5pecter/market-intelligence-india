"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthGate, CurrentUser } from "@/components/AuthGate";
import {
  DataBadge, Disclosure, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api, setToken } from "@/lib/api";
import { dateTimeIST, num, titleCase } from "@/lib/format";

export default function SettingsPage() {
  return <AuthGate>{(user) => <SettingsView user={user} />}</AuthGate>;
}

function SettingsView({ user }: { user: CurrentUser }) {
  const [environment, setEnvironment] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [prefs, setPrefs] = useState(user.notifications);
  const [message, setMessage] = useState<string | null>(null);
  const [password, setPassword] = useState({ current: "", next: "" });

  useEffect(() => {
    api.get<any>("/api/config/environment", false).then((r) => setEnvironment(r.data));
    api.get<any>("/api/health", false).then((r) => setHealth(r.data));
  }, []);

  const savePrefs = async (key: string, value: boolean) => {
    const next = { ...prefs, [key]: value };
    setPrefs(next);
    const response = await api.patch("/api/auth/notifications", {
      [`notify_${key}`]: value,
    });
    setMessage(response.error ? response.error : "Notification preferences saved.");
  };

  const changePassword = async () => {
    const response = await api.post("/api/auth/password", {
      current_password: password.current,
      new_password: password.next,
    });
    setMessage(response.error || "Password updated.");
    if (!response.error) setPassword({ current: "", next: "" });
  };

  return (
    <div className="space-y-3">
      <Panel
        title="Account"
        actions={
          <button
            className="btn"
            onClick={() => {
              setToken(null);
              window.location.reload();
            }}
          >
            Sign out
          </button>
        }
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="Email" value={user.email} />
          <Stat label="Display name" value={user.display_name} />
          <Stat label="Role" value={<Tag tone={user.role === "ADMIN" ? "accent" : "neutral"}>{user.role}</Tag>} />
        </div>
        {user.role === "ADMIN" && (
          <Link href="/admin" className="btn btn-accent mt-3">Open the admin panel</Link>
        )}
      </Panel>

      {message && <Notice tone="info">{message}</Notice>}

      <Panel title="Notification channels">
        <div className="space-y-2">
          {(["in_app", "email", "telegram", "browser_push"] as const).map((key) => (
            <label key={key} className="flex items-center gap-2 text-xs text-ink-dim">
              <input
                type="checkbox"
                checked={Boolean(prefs[key])}
                onChange={(event) => savePrefs(key, event.target.checked)}
              />
              {titleCase(key)}
            </label>
          ))}
        </div>
        <p className="mt-2 text-2xs text-ink-muted">
          A channel only delivers if the server has the credentials for it. The
          alerts page shows which are configured on this deployment.
        </p>
      </Panel>

      <Panel title="Change password">
        <div className="flex flex-wrap items-end gap-2">
          <label className="block">
            <span className="text-2xs text-ink-muted">Current password</span>
            <input
              type="password"
              value={password.current}
              onChange={(event) => setPassword({ ...password, current: event.target.value })}
              className="field mt-0.5 w-48"
              autoComplete="current-password"
            />
          </label>
          <label className="block">
            <span className="text-2xs text-ink-muted">New password (min 10)</span>
            <input
              type="password"
              value={password.next}
              onChange={(event) => setPassword({ ...password, next: event.target.value })}
              className="field mt-0.5 w-48"
              autoComplete="new-password"
            />
          </label>
          <button className="btn btn-accent" onClick={changePassword}>Update</button>
        </div>
      </Panel>

      <Panel title="This deployment" subtitle="No secrets are ever returned by the API">
        {environment ? (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Environment" value={environment.app_env} />
              <Stat label="Demo data visible" value={environment.demo_data_visible ? "Yes" : "No"} />
              <Stat label="Cache backend" value={environment.cache_backend} />
              <Stat label="Scheduler" value={environment.scheduler_enabled ? "Running" : "Off"} />
            </div>

            <div>
              <h3 className="mb-1 text-2xs uppercase tracking-wide text-ink-muted">
                Provider chains (first available wins)
              </h3>
              <ul className="space-y-1">
                {Object.entries(environment.provider_chains).map(([capability, chain]: [string, any]) => (
                  <li key={capability} className="flex items-center justify-between gap-3 text-xs">
                    <span className="text-ink-dim">{titleCase(capability)}</span>
                    <span className="num text-2xs text-ink-muted">
                      {chain.length ? chain.join(" → ") : "none configured"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <h3 className="mb-1 text-2xs uppercase tracking-wide text-ink-muted">
                Optional integrations
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(environment.optional_integrations).map(([name, enabled]) => (
                  <Tag key={name} tone={enabled ? "pos" : "neutral"}>
                    {titleCase(name)}: {enabled ? "configured" : "not configured"}
                  </Tag>
                ))}
              </div>
            </div>

            <Notice tone="warn">
              <strong>Known limitations of this configuration:</strong>
              <ul className="mt-1 list-inside list-disc space-y-0.5">
                {environment.limitations.map((limit: string) => (
                  <li key={limit}>{limit}</li>
                ))}
              </ul>
            </Notice>
          </div>
        ) : (
          <Loading />
        )}
      </Panel>

      <Panel
        title="System health"
        actions={health ? <Tag tone={health.status === "OK" ? "pos" : health.status === "DEGRADED" ? "warn" : "neg"}>{health.status}</Tag> : null}
      >
        {health ? (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat label="Database" value={health.database.status} tone={health.database.status === "OK" ? "pos" : "neg"} />
              <Stat label="Cache" value={`${health.cache.status} (${health.cache.backend})`} />
              <Stat label="Failed jobs (24h)" value={health.jobs.failed_last_24h} tone={health.jobs.failed_last_24h ? "warn" : undefined} />
            </div>

            <div className="scroll-x">
              <table className="w-full min-w-[820px]">
                <thead>
                  <tr>
                    <th className="th">Provider</th>
                    <th className="th">Status</th>
                    <th className="th">Circuit</th>
                    <th className="th">Capabilities</th>
                    <th className="th text-right">OK / fail</th>
                    <th className="th">Last success</th>
                    <th className="th">Last error</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {health.providers.map((provider: any) => (
                    <tr key={provider.name}>
                      <td className="td">
                        <span className="font-medium text-ink">{provider.display_name}</span>
                        {provider.is_delayed && (
                          <span className="ml-1 text-[9px] text-warn">DELAYED</span>
                        )}
                      </td>
                      <td className="td">
                        <Tag tone={provider.status === "OK" ? "pos" : provider.status === "DISABLED" ? "neutral" : "warn"}>
                          {provider.status}
                        </Tag>
                      </td>
                      <td className="td text-2xs">{provider.circuit_state}</td>
                      <td className="td max-w-[220px] truncate text-2xs text-ink-muted">
                        {provider.capabilities.join(", ")}
                      </td>
                      <td className="td num text-right text-2xs">
                        {provider.success_count} / {provider.failure_count}
                      </td>
                      <td className="td text-2xs text-ink-muted">
                        {dateTimeIST(provider.last_success_at)}
                      </td>
                      <td className="td max-w-[240px] truncate text-2xs text-neg" title={provider.last_error}>
                        {provider.last_error || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Disclosure summary="Recent background jobs" count={health.jobs.recent.length}>
              <div className="scroll-x">
                <table className="w-full min-w-[720px]">
                  <thead>
                    <tr>
                      <th className="th">Job</th>
                      <th className="th">Status</th>
                      <th className="th">Started</th>
                      <th className="th text-right">Duration</th>
                      <th className="th text-right">Saved</th>
                      <th className="th text-right">Rejected</th>
                      <th className="th">Error</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/50">
                    {health.jobs.recent.map((job: any, index: number) => (
                      <tr key={index}>
                        <td className="td text-ink">{job.job}</td>
                        <td className="td">
                          <Tag tone={job.status === "SUCCESS" ? "pos" : job.status === "PARTIAL" ? "warn" : "neg"}>
                            {job.status}
                          </Tag>
                        </td>
                        <td className="td text-2xs">{dateTimeIST(job.started_at)}</td>
                        <td className="td num text-right text-2xs">{num(job.duration_ms, 0)} ms</td>
                        <td className="td num text-right">{job.records_saved}</td>
                        <td className="td num text-right text-warn">{job.records_rejected}</td>
                        <td className="td max-w-[240px] truncate text-2xs text-neg">{job.error || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Disclosure>
          </div>
        ) : (
          <Loading />
        )}
      </Panel>
    </div>
  );
}
