"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthGate } from "@/components/AuthGate";
import { DataBadge, Empty, Loading, Notice, Panel, Tag } from "@/components/primitives";
import { api } from "@/lib/api";
import { DASH, dateIST, num, pct, signClass } from "@/lib/format";

export default function WatchlistPage() {
  return <AuthGate>{() => <WatchlistView />}</AuthGate>;
}

function WatchlistView() {
  const [lists, setLists] = useState<any[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [newList, setNewList] = useState("");
  const [newSymbol, setNewSymbol] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadLists = async () => {
    const response = await api.get<{ watchlists: any[] }>("/api/watchlists");
    if (response.data) {
      setLists(response.data.watchlists);
      if (!activeId && response.data.watchlists.length) {
        setActiveId(response.data.watchlists[0].id);
      }
    }
    setLoading(false);
  };

  const loadDetail = async (id: string) => {
    const response = await api.get<any>(`/api/watchlists/${id}`);
    setDetail(response.data ?? { error: response.error });
  };

  useEffect(() => {
    loadLists();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeId) loadDetail(activeId);
  }, [activeId]);

  const createList = async () => {
    if (!newList.trim()) return;
    const response = await api.post("/api/watchlists", { name: newList.trim() });
    if (response.error) setError(response.error);
    else {
      setNewList("");
      setError(null);
      loadLists();
    }
  };

  const addSymbol = async () => {
    if (!activeId || !newSymbol.trim()) return;
    const response = await api.post(`/api/watchlists/${activeId}/items`, {
      symbol: newSymbol.trim().toUpperCase(),
      segment: "EQUITY",
    });
    if (response.error) setError(response.error);
    else {
      setNewSymbol("");
      setError(null);
      loadDetail(activeId);
    }
  };

  const removeItem = async (itemId: string) => {
    if (!activeId) return;
    await api.del(`/api/watchlists/${activeId}/items/${itemId}`);
    loadDetail(activeId);
  };

  if (loading) return <Panel><Loading /></Panel>;

  return (
    <div className="space-y-3">
      <Panel
        title="Watchlists"
        actions={
          <div className="flex gap-1.5">
            <input
              value={newList}
              onChange={(e) => setNewList(e.target.value)}
              placeholder="New watchlist name"
              className="field w-44"
            />
            <button className="btn" onClick={createList}>Create</button>
          </div>
        }
      >
        {error && <Notice tone="neg">{error}</Notice>}
        <div className="flex flex-wrap gap-1.5">
          {lists.map((list) => (
            <button
              key={list.id}
              onClick={() => setActiveId(list.id)}
              className={
                activeId === list.id
                  ? "chip border-accent/50 bg-accent/10 text-accent"
                  : "chip border-line bg-raised text-ink-muted hover:text-ink"
              }
            >
              {list.name} · {list.item_count}
            </button>
          ))}
          {lists.length === 0 && (
            <p className="text-2xs text-ink-muted">No watchlists yet.</p>
          )}
        </div>
      </Panel>

      {detail && !detail.error && (
        <Panel
          title={detail.name}
          subtitle={
            detail.indicator_snapshot_date
              ? `Indicator columns come from the ${dateIST(detail.indicator_snapshot_date)} snapshot, not live`
              : "No indicator snapshot exists yet — run indicator_refresh"
          }
          actions={
            <div className="flex gap-1.5">
              <input
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value)}
                placeholder="Add symbol"
                className="field w-32"
              />
              <button className="btn btn-accent" onClick={addSymbol}>Add</button>
            </div>
          }
          bodyClassName="p-0"
        >
          {detail.items?.length === 0 ? (
            <div className="p-4"><Empty message="This watchlist is empty." /></div>
          ) : (
            <div className="scroll-x">
              <table className="w-full min-w-[1080px]">
                <thead className="border-b border-line">
                  <tr>
                    <th className="th">Symbol</th>
                    <th className="th text-right">LTP</th>
                    <th className="th text-right">Change</th>
                    <th className="th text-right">RSI</th>
                    <th className="th text-right">Vol ×</th>
                    <th className="th">Trend</th>
                    <th className="th text-right">Trend score</th>
                    <th className="th">Signal</th>
                    <th className="th">Risk</th>
                    <th className="th">Latest news</th>
                    <th className="th">Next event</th>
                    <th className="th">Data</th>
                    <th className="th"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {detail.items.map((item: any) => (
                    <tr key={item.id} className="hover:bg-raised/40">
                      <td className="td">
                        <Link href={`/stocks/${item.symbol}`} className="font-semibold text-ink hover:text-accent">
                          {item.symbol}
                        </Link>
                      </td>
                      <td className="td num text-right">{num(item.ltp)}</td>
                      <td className={`td num text-right ${signClass(item.change_pct)}`}>
                        {pct(item.change_pct)}
                      </td>
                      <td className="td num text-right">{num(item.rsi_14, 1)}</td>
                      <td className="td num text-right">{num(item.volume_ratio, 2)}</td>
                      <td className="td">
                        {item.trend ? (
                          <Tag tone={item.trend === "UP" ? "pos" : "neg"}>{item.trend}</Tag>
                        ) : DASH}
                      </td>
                      <td className="td num text-right">{num(item.research_score, 0)}</td>
                      <td className="td">
                        {item.signal ? (
                          <Tag tone={item.signal === "BUY" ? "pos" : "neg"}>
                            {item.signal_status?.replace(/_/g, " ") || item.signal}
                          </Tag>
                        ) : DASH}
                      </td>
                      <td className="td text-2xs">{item.risk || DASH}</td>
                      <td className="td max-w-[220px] truncate text-2xs text-ink-muted" title={item.latest_news?.headline}>
                        {item.latest_news?.headline || DASH}
                      </td>
                      <td className="td max-w-[160px] truncate text-2xs text-ink-muted">
                        {item.upcoming_event
                          ? `${item.upcoming_event.title} (${dateIST(item.upcoming_event.date)})`
                          : DASH}
                      </td>
                      <td className="td"><DataBadge status={item.data_status} compact /></td>
                      <td className="td">
                        <button
                          className="btn px-2 py-0.5"
                          onClick={() => removeItem(item.id)}
                          aria-label={`Remove ${item.symbol}`}
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
