"use client";

import { useEffect, useState } from "react";
import { AuthGate } from "@/components/AuthGate";
import { Disclosure, Empty, Loading, Notice, Panel, Tag } from "@/components/primitives";
import { api } from "@/lib/api";
import { dateTimeIST, num, titleCase } from "@/lib/format";

export default function AlertsPage() {
  return <AuthGate>{() => <AlertsView />}</AuthGate>;
}

function AlertsView() {
  const [types, setTypes] = useState<any[]>([]);
  const [channels, setChannels] = useState<Record<string, any>>({});
  const [alerts, setAlerts] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [alertType, setAlertType] = useState("PRICE_ABOVE");
  const [symbol, setSymbol] = useState("");
  const [threshold, setThreshold] = useState("");
  const [keyword, setKeyword] = useState("");
  const [selectedChannels, setSelectedChannels] = useState<string[]>(["in_app"]);

  const load = async () => {
    const [typesResponse, alertsResponse, eventsResponse] = await Promise.all([
      api.get<any>("/api/alerts/types"),
      api.get<any>("/api/alerts"),
      api.get<any>("/api/alerts/events?limit=40"),
    ]);
    if (typesResponse.data) {
      setTypes(typesResponse.data.types);
      setChannels(typesResponse.data.channels);
    }
    if (alertsResponse.data) setAlerts(alertsResponse.data.alerts);
    if (eventsResponse.data) setEvents(eventsResponse.data.events);
    setLoading(false);
  };

  useEffect(() => {
    // Prefill from ?symbol= when arriving from a stock page.
    const params = new URLSearchParams(window.location.search);
    if (params.get("symbol")) setSymbol(params.get("symbol")!.toUpperCase());
    load();
  }, []);

  const definition = types.find((t) => t.key === alertType);
  const needsThreshold = definition?.fields?.includes("threshold");
  const needsKeyword = definition?.fields?.includes("keyword");

  const create = async () => {
    setError(null);
    const condition: Record<string, unknown> = {};
    if (needsThreshold) condition.threshold = Number(threshold);
    if (needsKeyword) condition.keyword = keyword;

    const response = await api.post("/api/alerts", {
      alert_type: alertType,
      symbol: symbol || null,
      condition,
      channels: selectedChannels,
    });
    if (response.error) setError(response.error);
    else {
      setThreshold("");
      setKeyword("");
      load();
    }
  };

  const remove = async (id: string) => {
    await api.del(`/api/alerts/${id}`);
    load();
  };

  const markRead = async (id: string) => {
    await api.post(`/api/alerts/events/${id}/read`);
    load();
  };

  if (loading) return <Panel><Loading /></Panel>;

  return (
    <div className="space-y-3">
      <Panel title="Create an alert">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block">
            <span className="text-2xs text-ink-muted">Condition</span>
            <select
              value={alertType}
              onChange={(e) => setAlertType(e.target.value)}
              className="field mt-0.5"
            >
              {types.map((type) => (
                <option key={type.key} value={type.key}>{type.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-2xs text-ink-muted">Symbol</span>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="field mt-0.5"
              placeholder="e.g. HDFCBANK"
            />
          </label>
          {needsThreshold && (
            <label className="block">
              <span className="text-2xs text-ink-muted">Threshold</span>
              <input
                type="number"
                step="any"
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
                className="field mt-0.5"
              />
            </label>
          )}
          {needsKeyword && (
            <label className="block">
              <span className="text-2xs text-ink-muted">Keyword</span>
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                className="field mt-0.5"
              />
            </label>
          )}
          <div className="flex items-end">
            <button className="btn btn-accent w-full" onClick={create}>
              Create alert
            </button>
          </div>
        </div>

        <div className="mt-3">
          <span className="text-2xs text-ink-muted">Delivery channels</span>
          <div className="mt-1 flex flex-wrap gap-2">
            {Object.entries(channels).map(([name, info]: [string, any]) => (
              <label
                key={name}
                className={
                  info.available
                    ? "chip cursor-pointer border-line bg-raised text-ink-dim"
                    : "chip cursor-not-allowed border-line bg-raised text-ink-muted opacity-60"
                }
                title={info.available ? undefined : `Needs: ${info.requires}`}
              >
                <input
                  type="checkbox"
                  disabled={!info.available}
                  checked={selectedChannels.includes(name)}
                  onChange={(e) =>
                    setSelectedChannels(
                      e.target.checked
                        ? [...selectedChannels, name]
                        : selectedChannels.filter((c) => c !== name),
                    )
                  }
                />
                {titleCase(name)}
                {!info.available && " (not configured)"}
              </label>
            ))}
          </div>
        </div>

        {error && <Notice tone="neg">{error}</Notice>}
      </Panel>

      <Panel title="Your alerts" bodyClassName="p-0">
        {alerts.length === 0 ? (
          <div className="p-4"><Empty message="No alerts configured." /></div>
        ) : (
          <div className="scroll-x">
            <table className="w-full min-w-[880px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Name</th>
                  <th className="th">Symbol</th>
                  <th className="th">Condition</th>
                  <th className="th">Channels</th>
                  <th className="th">Active</th>
                  <th className="th text-right">Triggers</th>
                  <th className="th">Last evaluated</th>
                  <th className="th">Last result</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {alerts.map((alert) => (
                  <tr key={alert.id}>
                    <td className="td max-w-[220px] truncate text-ink">{alert.name}</td>
                    <td className="td">{alert.symbol || "—"}</td>
                    <td className="num td text-2xs text-ink-dim">
                      {alert.alert_type} {JSON.stringify(alert.condition)}
                    </td>
                    <td className="td text-2xs text-ink-muted">{alert.channels.join(", ")}</td>
                    <td className="td">
                      <Tag tone={alert.is_active ? "pos" : "neutral"}>
                        {alert.is_active ? "Active" : "Done"}
                      </Tag>
                    </td>
                    <td className="td num text-right">{alert.trigger_count}</td>
                    <td className="td text-2xs text-ink-muted">
                      {dateTimeIST(alert.last_evaluated_at)}
                    </td>
                    <td className="td max-w-[260px] whitespace-normal text-2xs text-ink-muted">
                      {alert.last_evaluation_note || "—"}
                    </td>
                    <td className="td">
                      <button className="btn px-2 py-0.5" onClick={() => remove(alert.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Notifications">
        {events.length === 0 ? (
          <Empty message="Nothing has triggered yet." />
        ) : (
          <ul className="divide-y divide-line/60">
            {events.map((event) => (
              <li key={event.id} className={event.is_read ? "py-2.5 opacity-60" : "py-2.5"}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-ink">{event.title}</p>
                    <p className="mt-0.5 text-2xs leading-relaxed text-ink-dim">{event.body}</p>
                    <p className="mt-0.5 text-2xs text-ink-muted">
                      {dateTimeIST(event.created_at)} · delivery {event.delivery_status}
                    </p>
                  </div>
                  {!event.is_read && (
                    <button className="btn shrink-0 px-2 py-0.5" onClick={() => markRead(event.id)}>
                      Mark read
                    </button>
                  )}
                </div>
                {event.evidence && Object.keys(event.evidence).length > 0 && (
                  <Disclosure summary="Evidence behind this trigger">
                    <pre className="num overflow-x-auto whitespace-pre-wrap text-2xs text-ink-muted">
                      {JSON.stringify(event.evidence, null, 2)}
                    </pre>
                  </Disclosure>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
