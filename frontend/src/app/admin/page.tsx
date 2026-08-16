"use client";

import { useEffect, useState } from "react";
import { AuthGate, CurrentUser } from "@/components/AuthGate";
import {
  Disclosure, Empty, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { dateTimeIST, num, titleCase } from "@/lib/format";

const JOBS = [
  "instrument_sync", "quote_refresh", "history_refresh", "indicator_refresh",
  "news_refresh", "research_status_update", "alert_engine",
  "end_of_day_snapshot",
];

export default function AdminPage() {
  return <AuthGate requireRole="ADMIN">{(user) => <AdminView user={user} />}</AuthGate>;
}

function AdminView({ user }: { user: CurrentUser }) {
  const [tab, setTab] = useState<"jobs" | "research" | "users" | "audit" | "data">("jobs");

  return (
    <div className="space-y-3">
      <Panel title="Admin" subtitle={`Signed in as ${user.email}`}>
        <div className="flex flex-wrap gap-1">
          {(["jobs", "research", "users", "audit", "data"] as const).map((key) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={
                tab === key
                  ? "chip border-accent/50 bg-accent/10 text-accent"
                  : "chip border-line bg-raised text-ink-muted hover:text-ink"
              }
            >
              {titleCase(key)}
            </button>
          ))}
        </div>
      </Panel>

      {tab === "jobs" && <JobsTab />}
      {tab === "research" && <ResearchTab />}
      {tab === "users" && <UsersTab currentUserId={user.id} />}
      {tab === "audit" && <AuditTab />}
      {tab === "data" && <DataEntryTab />}
    </div>
  );
}

function JobsTab() {
  const [runs, setRuns] = useState<any[]>([]);
  const [providers, setProviders] = useState<any>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  const load = async () => {
    const [jobsResponse, providersResponse] = await Promise.all([
      api.get<any>("/api/admin/jobs?limit=40"),
      api.get<any>("/api/admin/providers"),
    ]);
    if (jobsResponse.data) setRuns(jobsResponse.data.runs);
    if (providersResponse.data) setProviders(providersResponse.data);
  };

  useEffect(() => {
    load();
  }, []);

  const run = async (job: string) => {
    setRunning(job);
    setResult(null);
    const response = await api.post<any>(`/api/admin/jobs/${job}/run`);
    setResult(response.data ?? { error: response.error });
    setRunning(null);
    load();
  };

  return (
    <div className="space-y-3">
      <Panel title="Run a job now">
        <div className="flex flex-wrap gap-1.5">
          {JOBS.map((job) => (
            <button
              key={job}
              className="btn"
              disabled={running === job}
              onClick={() => run(job)}
            >
              {running === job ? "Running…" : job}
            </button>
          ))}
        </div>
        {result && (
          <pre className="num mt-3 overflow-x-auto rounded border border-line bg-bg p-2.5 text-2xs text-ink-dim">
            {JSON.stringify(result, null, 2)}
          </pre>
        )}
      </Panel>

      {providers && (
        <Panel title="Data providers" subtitle={providers.note} bodyClassName="p-0">
          <div className="scroll-x">
            <table className="w-full min-w-[900px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Provider</th>
                  <th className="th">Enabled</th>
                  <th className="th">Status</th>
                  <th className="th">Delayed</th>
                  <th className="th">Rate limit</th>
                  <th className="th">In chains</th>
                  <th className="th text-right">Calls (1h)</th>
                  <th className="th">Terms</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {providers.providers.map((provider: any) => (
                  <tr key={provider.name}>
                    <td className="td">
                      <span className="font-medium text-ink">{provider.display_name}</span>
                      <p className="whitespace-normal text-2xs text-ink-muted">{provider.licence}</p>
                    </td>
                    <td className="td">
                      <Tag tone={provider.enabled ? "pos" : "neutral"}>
                        {provider.enabled ? "Yes" : "No"}
                      </Tag>
                    </td>
                    <td className="td">
                      <Tag tone={provider.status === "OK" ? "pos" : provider.status === "DISABLED" ? "neutral" : "warn"}>
                        {provider.status}
                      </Tag>
                    </td>
                    <td className="td text-2xs">{provider.is_delayed ? "Yes" : "No"}</td>
                    <td className="td num text-2xs">{provider.rate_limit_per_minute}/min</td>
                    <td className="td text-2xs text-ink-muted">
                      {provider.in_chains.join(", ") || "—"}
                    </td>
                    <td className="td num text-right">{provider.calls_last_hour}</td>
                    <td className="td text-2xs">
                      {provider.terms_url ? (
                        <a href={provider.terms_url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                          Terms
                        </a>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel title="Recent job runs" bodyClassName="p-0">
        <div className="scroll-x">
          <table className="w-full min-w-[820px]">
            <thead className="border-b border-line">
              <tr>
                <th className="th">Job</th>
                <th className="th">Status</th>
                <th className="th">Started</th>
                <th className="th text-right">Duration</th>
                <th className="th text-right">Received</th>
                <th className="th text-right">Saved</th>
                <th className="th text-right">Rejected</th>
                <th className="th">Error</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {runs.map((run) => (
                <tr key={run.id}>
                  <td className="td text-ink">{run.job}</td>
                  <td className="td">
                    <Tag tone={run.status === "SUCCESS" ? "pos" : run.status === "PARTIAL" ? "warn" : "neg"}>
                      {run.status}
                    </Tag>
                  </td>
                  <td className="td text-2xs">{dateTimeIST(run.started_at)}</td>
                  <td className="td num text-right text-2xs">{num(run.duration_ms, 0)} ms</td>
                  <td className="td num text-right">{run.records_received}</td>
                  <td className="td num text-right text-pos">{run.records_saved}</td>
                  <td className="td num text-right text-warn">{run.records_rejected}</td>
                  <td className="td max-w-[280px] truncate text-2xs text-neg">{run.error || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function ResearchTab() {
  const [pending, setPending] = useState<any[]>([]);
  const [sources, setSources] = useState<any[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [form, setForm] = useState({
    symbol: "", company_name: "", segment: "EQUITY", side: "BUY",
    source_type: "EXTERNAL_RESEARCH", source_name: "", analyst_name: "",
    original_url: "", entry_min: "", entry_max: "", stop_loss: "",
    target_1: "", rationale: "", invalidation: "",
  });

  const load = async () => {
    const [pendingResponse, sourcesResponse] = await Promise.all([
      api.get<any>("/api/admin/research-calls/pending"),
      api.get<any>("/api/admin/sources"),
    ]);
    if (pendingResponse.data) setPending(pendingResponse.data.calls);
    if (sourcesResponse.data) setSources(sourcesResponse.data.sources);
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    setMessage(null);
    const payload: Record<string, unknown> = { ...form };
    for (const key of ["entry_min", "entry_max", "stop_loss", "target_1"]) {
      payload[key] = form[key as keyof typeof form] ? Number(form[key as keyof typeof form]) : null;
    }
    const response = await api.post("/api/admin/research-call", payload);
    setMessage(response.error || "Research call created (unpublished until approved).");
    if (!response.error) load();
  };

  const approve = async (id: string) => {
    const response = await api.post(`/api/admin/research-call/${id}/approve`);
    setMessage(response.error || "Published.");
    load();
  };

  return (
    <div className="space-y-3">
      <Panel title="Create a research call">
        <Notice tone="info">
          A call attributed to a third party must name its source. The platform
          refuses to present someone else&rsquo;s research as its own, and it
          rejects any text containing a prohibited claim.
        </Notice>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {([
            ["symbol", "Symbol"], ["company_name", "Company"],
            ["source_name", "Source name"], ["analyst_name", "Analyst (optional)"],
            ["original_url", "Original URL (optional)"],
            ["entry_min", "Entry min"], ["entry_max", "Entry max"],
            ["stop_loss", "Stop loss"], ["target_1", "Target 1"],
          ] as const).map(([key, label]) => (
            <label key={key} className="block">
              <span className="text-2xs text-ink-muted">{label}</span>
              <input
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                className="field mt-0.5"
              />
            </label>
          ))}
          <label className="block">
            <span className="text-2xs text-ink-muted">Segment</span>
            <select
              value={form.segment}
              onChange={(e) => setForm({ ...form, segment: e.target.value })}
              className="field mt-0.5"
            >
              <option value="EQUITY">Equity</option>
              <option value="OPTION">Option</option>
              <option value="FUTURE">Future</option>
            </select>
          </label>
          <label className="block">
            <span className="text-2xs text-ink-muted">Side</span>
            <select
              value={form.side}
              onChange={(e) => setForm({ ...form, side: e.target.value })}
              className="field mt-0.5"
            >
              <option value="BUY">Buy</option>
              <option value="SELL">Sell</option>
              <option value="WATCH">Watch</option>
            </select>
          </label>
          <label className="block">
            <span className="text-2xs text-ink-muted">Origin</span>
            <select
              value={form.source_type}
              onChange={(e) => setForm({ ...form, source_type: e.target.value })}
              className="field mt-0.5"
            >
              <option value="EXTERNAL_RESEARCH">Third-party research</option>
              <option value="PLATFORM_GENERATED">Platform generated</option>
            </select>
          </label>
        </div>
        <div className="mt-2 grid gap-2 lg:grid-cols-2">
          <label className="block">
            <span className="text-2xs text-ink-muted">Rationale</span>
            <textarea
              value={form.rationale}
              onChange={(e) => setForm({ ...form, rationale: e.target.value })}
              className="field mt-0.5 h-20"
            />
          </label>
          <label className="block">
            <span className="text-2xs text-ink-muted">What would invalidate it</span>
            <textarea
              value={form.invalidation}
              onChange={(e) => setForm({ ...form, invalidation: e.target.value })}
              className="field mt-0.5 h-20"
            />
          </label>
        </div>
        <button className="btn btn-accent mt-2" onClick={create}>Create</button>
        {message && <Notice tone={message.includes("created") || message.includes("Published") ? "info" : "neg"}>{message}</Notice>}
      </Panel>

      <Panel title="Pending approval" bodyClassName="p-0">
        {pending.length === 0 ? (
          <div className="p-4"><Empty message="Nothing awaiting approval." /></div>
        ) : (
          <div className="scroll-x">
            <table className="w-full min-w-[720px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Symbol</th>
                  <th className="th">Side</th>
                  <th className="th">Segment</th>
                  <th className="th">Origin</th>
                  <th className="th">Source</th>
                  <th className="th">Created</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {pending.map((call) => (
                  <tr key={call.id}>
                    <td className="td font-semibold text-ink">{call.symbol}</td>
                    <td className="td"><Tag tone={call.side === "BUY" ? "pos" : "neg"}>{call.side}</Tag></td>
                    <td className="td text-2xs">{call.segment}</td>
                    <td className="td text-2xs">{call.source_type}</td>
                    <td className="td max-w-[220px] truncate text-2xs text-ink-muted">{call.source_name}</td>
                    <td className="td text-2xs">{dateTimeIST(call.created_at)}</td>
                    <td className="td">
                      <button className="btn px-2 py-0.5" onClick={() => approve(call.id)}>
                        Approve &amp; publish
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Research sources">
        {sources.length === 0 ? (
          <Empty message="No sources registered." />
        ) : (
          <ul className="space-y-1.5">
            {sources.map((source) => (
              <li key={source.id} className="rounded border border-line bg-raised/30 p-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-ink">{source.name}</span>
                  <Tag tone="neutral">{source.reliability}</Tag>
                  <Tag tone={source.source_type === "EXTERNAL_RESEARCH" ? "info" : "accent"}>
                    {source.source_type}
                  </Tag>
                </div>
                {source.registration_note && (
                  <p className="mt-0.5 text-2xs text-ink-muted">{source.registration_note}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function UsersTab({ currentUserId }: { currentUserId: string }) {
  const [users, setUsers] = useState<any[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    const response = await api.get<any>("/api/admin/users");
    if (response.data) setUsers(response.data.users);
  };

  useEffect(() => {
    load();
  }, []);

  const update = async (id: string, changes: Record<string, unknown>) => {
    const reason = window.prompt("Reason for this change (recorded in the audit log):");
    if (!reason) return;
    const response = await api.patch(`/api/admin/users/${id}`, { ...changes, reason });
    setMessage(response.error || "Updated.");
    load();
  };

  return (
    <Panel title="Users" bodyClassName="p-0">
      {message && <div className="p-3"><Notice tone="info">{message}</Notice></div>}
      <div className="scroll-x">
        <table className="w-full min-w-[720px]">
          <thead className="border-b border-line">
            <tr>
              <th className="th">Email</th>
              <th className="th">Name</th>
              <th className="th">Role</th>
              <th className="th">Active</th>
              <th className="th">Created</th>
              <th className="th">Last login</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/50">
            {users.map((user) => (
              <tr key={user.id}>
                <td className="td text-ink">{user.email}</td>
                <td className="td text-ink-dim">{user.display_name}</td>
                <td className="td">
                  <select
                    value={user.role}
                    onChange={(e) => update(user.id, { role: e.target.value })}
                    className="field w-28 py-0.5"
                  >
                    <option value="USER">USER</option>
                    <option value="ANALYST">ANALYST</option>
                    <option value="ADMIN">ADMIN</option>
                  </select>
                </td>
                <td className="td">
                  <Tag tone={user.is_active ? "pos" : "neg"}>
                    {user.is_active ? "Active" : "Disabled"}
                  </Tag>
                </td>
                <td className="td text-2xs">{dateTimeIST(user.created_at)}</td>
                <td className="td text-2xs text-ink-muted">{dateTimeIST(user.last_login_at)}</td>
                <td className="td">
                  {user.id !== currentUserId && (
                    <button
                      className="btn px-2 py-0.5"
                      onClick={() => update(user.id, { is_active: !user.is_active })}
                    >
                      {user.is_active ? "Disable" : "Enable"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function AuditTab() {
  const [entries, setEntries] = useState<any[]>([]);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.get<any>("/api/admin/audit?limit=150").then((response) => {
      if (response.data) {
        setEntries(response.data.entries);
        setNote(response.data.note);
      }
    });
  }, []);

  return (
    <Panel title="Audit log" subtitle={note ?? undefined} bodyClassName="p-0">
      <div className="scroll-x max-h-[600px] overflow-y-auto">
        <table className="w-full min-w-[860px]">
          <thead className="sticky top-0 border-b border-line bg-surface">
            <tr>
              <th className="th">When</th>
              <th className="th">Action</th>
              <th className="th">Entity</th>
              <th className="th">Actor</th>
              <th className="th">Reason</th>
              <th className="th">Change</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/50">
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td className="td text-2xs">{dateTimeIST(entry.created_at)}</td>
                <td className="td text-2xs text-ink">{entry.action}</td>
                <td className="td text-2xs text-ink-muted">
                  {entry.entity_type}
                  {entry.entity_id && <div className="num truncate">{entry.entity_id.slice(0, 8)}</div>}
                </td>
                <td className="td text-2xs text-ink-dim">
                  {entry.actor_email || "system"}
                  {entry.actor_role && <div className="text-ink-muted">{entry.actor_role}</div>}
                </td>
                <td className="td max-w-[220px] whitespace-normal text-2xs text-ink-muted">
                  {entry.reason || "—"}
                </td>
                <td className="td max-w-[280px] text-2xs">
                  {entry.old_value || entry.new_value ? (
                    <Disclosure summary="View diff">
                      <pre className="num overflow-x-auto whitespace-pre-wrap text-[10px] text-ink-muted">
                        {JSON.stringify({ from: entry.old_value, to: entry.new_value }, null, 2)}
                      </pre>
                    </Disclosure>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function DataEntryTab() {
  const [instrument, setInstrument] = useState({
    symbol: "", name: "", exchange_code: "NSE", sector: "", lot_size: "",
  });
  const [quote, setQuote] = useState({
    symbol: "", ltp: "", previous_close: "", source_name: "Operator entry",
  });
  const [message, setMessage] = useState<string | null>(null);

  const addInstrument = async () => {
    const response = await api.post("/api/admin/instruments", {
      ...instrument,
      lot_size: instrument.lot_size ? Number(instrument.lot_size) : null,
    });
    setMessage(response.error || "Instrument added.");
  };

  const setManualQuote = async () => {
    const response = await api.post("/api/admin/quotes", {
      symbol: quote.symbol.toUpperCase(),
      ltp: Number(quote.ltp),
      previous_close: quote.previous_close ? Number(quote.previous_close) : null,
      source_name: quote.source_name,
    });
    setMessage(response.error || "Quote stored with status MANUAL.");
  };

  return (
    <div className="space-y-3">
      {message && <Notice tone={message.includes("added") || message.includes("stored") ? "info" : "neg"}>{message}</Notice>}

      <Panel title="Add an instrument">
        <div className="flex flex-wrap items-end gap-2">
          {([
            ["symbol", "Symbol"], ["name", "Name"], ["sector", "Sector"],
            ["lot_size", "Lot size"],
          ] as const).map(([key, label]) => (
            <label key={key} className="block">
              <span className="text-2xs text-ink-muted">{label}</span>
              <input
                value={instrument[key]}
                onChange={(e) => setInstrument({ ...instrument, [key]: e.target.value })}
                className="field mt-0.5 w-36"
              />
            </label>
          ))}
          <label className="block">
            <span className="text-2xs text-ink-muted">Exchange</span>
            <select
              value={instrument.exchange_code}
              onChange={(e) => setInstrument({ ...instrument, exchange_code: e.target.value })}
              className="field mt-0.5 w-24"
            >
              <option value="NSE">NSE</option>
              <option value="BSE">BSE</option>
            </select>
          </label>
          <button className="btn btn-accent" onClick={addInstrument}>Add</button>
        </div>
      </Panel>

      <Panel
        title="Set a manual quote"
        subtitle="Stored with data_status = MANUAL and badged as such everywhere it appears"
      >
        <div className="flex flex-wrap items-end gap-2">
          {([
            ["symbol", "Symbol"], ["ltp", "Last price"],
            ["previous_close", "Previous close"], ["source_name", "Source"],
          ] as const).map(([key, label]) => (
            <label key={key} className="block">
              <span className="text-2xs text-ink-muted">{label}</span>
              <input
                value={quote[key]}
                onChange={(e) => setQuote({ ...quote, [key]: e.target.value })}
                className="field mt-0.5 w-36"
              />
            </label>
          ))}
          <button className="btn btn-accent" onClick={setManualQuote}>Save</button>
        </div>
      </Panel>
    </div>
  );
}
