"use client";

/**
 * The official exchange record: breadth, delivery and disclosed deals.
 *
 * Everything here comes from a file the exchange itself published, so it is
 * authoritative and end-of-day at the same time. The session date is shown on
 * every panel rather than implied, because "today's data" that is actually
 * Friday's is how a research tool quietly misleads you.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Empty, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { compactInr, num, pct, signClass } from "@/lib/format";

interface Breadth {
  session_date: string;
  scrips_counted: number;
  advances: number;
  declines: number;
  unchanged: number;
  advance_decline_ratio: number | null;
  median_change_pct: number;
  pct_up_more_than_2: number;
  pct_down_more_than_2: number;
  interpretation: string;
}

interface DeliveryRow {
  symbol: string;
  delivery_pct: number;
  close: number | null;
  change_pct: number | null;
  turnover: number | null;
}

interface DealFlow {
  symbol: string;
  direction: string;
  net_value: number;
  deal_count: number;
  buyers: string[];
  sellers: string[];
  note: string;
}

function Provenance({ p }: { p: any }) {
  if (!p) return null;
  return (
    <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
      <span className="text-ink-dim">{p.source}</span>
      {p.status ? <> · {p.status}</> : null}
      {p.observed_at ? <> · session {String(p.observed_at).slice(0, 10)}</> : null}
      {p.notes ? <> · {p.notes}</> : null}
    </p>
  );
}

export default function ExchangePage() {
  const [breadth, setBreadth] = useState<any>(null);
  const [delivery, setDelivery] = useState<any>(null);
  const [deals, setDeals] = useState<any>(null);
  const [dealKind, setDealKind] = useState<"bulk" | "block">("bulk");
  const [minTurnover, setMinTurnover] = useState(100_000_000);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.get<any>("/api/exchange/breadth", false),
      api.get<any>(
        `/api/exchange/delivery?limit=25&min_turnover=${minTurnover}`, false),
    ]).then(([b, d]) => {
      setBreadth(b.data ?? { error: b.error });
      setDelivery(d.data ?? { error: d.error });
      setLoading(false);
    });
  }, [minTurnover]);

  useEffect(() => {
    api.get<any>(`/api/exchange/deals?kind=${dealKind}`, false)
      .then((r) => setDeals(r.data ?? { error: r.error }));
  }, [dealKind]);

  const b: Breadth | undefined = breadth?.breadth;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold tracking-tight text-ink">
          Exchange record
        </h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-dim">
          Published NSE and BSE end-of-day files: settled prices, the
          securities-wise delivery report and disclosed bulk and block deals.
          This is the exchange&apos;s own record, not a vendor&apos;s
          reconstruction of it.
        </p>
      </header>

      <Notice tone="info">
        These files are published after the close, so they are authoritative and
        end-of-day at the same time. Every panel states the session it
        describes. Nothing here is a forecast.
      </Notice>

      {loading ? <Loading label="Loading the exchange record" /> : null}

      <Panel
        title="Market breadth"
        subtitle={b ? `Session ${b.session_date} · ${num(b.scrips_counted)} scrips` : undefined}
      >
        {breadth?.error ? (
          <Unavailable reason={breadth.error} />
        ) : b ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Advances" value={num(b.advances)} tone="pos" />
              <Stat label="Declines" value={num(b.declines)} tone="neg" />
              <Stat
                label="A/D ratio"
                value={b.advance_decline_ratio?.toFixed(2) ?? "—"}
                tone={(b.advance_decline_ratio ?? 1) >= 1 ? "pos" : "neg"}
                hint="Advancing scrips divided by declining scrips."
              />
              <Stat
                label="Median scrip"
                value={pct(b.median_change_pct)}
                tone={b.median_change_pct >= 0 ? "pos" : "neg"}
                hint="What the typical stock did, regardless of the headline index."
              />
            </div>
            <div className="mt-3 grid grid-cols-3 gap-3">
              <Stat label="Up over 2%" value={`${b.pct_up_more_than_2}%`} />
              <Stat label="Down over 2%" value={`${b.pct_down_more_than_2}%`} />
              <Stat label="Unchanged" value={num(b.unchanged)} tone="muted" />
            </div>
            <p className="mt-3 text-xs leading-relaxed text-ink-dim">
              {b.interpretation}
            </p>
            <Provenance p={breadth.provenance} />
          </>
        ) : (
          <Empty message="No breadth data for this session." />
        )}
      </Panel>

      <Panel
        title="Delivery leaders"
        subtitle={delivery?.session_date ? `Session ${delivery.session_date}` : undefined}
        actions={
          <select
            value={minTurnover}
            onChange={(e) => setMinTurnover(Number(e.target.value))}
            aria-label="Minimum turnover"
            className="rounded border border-line-strong bg-raised px-2 py-1 text-2xs text-ink"
          >
            <option value={0}>No turnover floor</option>
            <option value={10_000_000}>Over ₹1 cr</option>
            <option value={100_000_000}>Over ₹10 cr</option>
            <option value={1_000_000_000}>Over ₹100 cr</option>
          </select>
        }
      >
        {delivery?.error ? (
          <Unavailable
            reason={delivery.error}
            hint={["Delivery data is stored locally by the ingestion job.",
                   "An admin can populate it with POST /api/exchange/ingest."]}
          />
        ) : delivery?.rows?.length ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-line text-2xs uppercase tracking-wide text-ink-muted">
                    <th className="py-1.5 text-left font-medium">Symbol</th>
                    <th className="py-1.5 text-right font-medium">Delivery</th>
                    <th className="py-1.5 text-right font-medium">Close</th>
                    <th className="py-1.5 text-right font-medium">Change</th>
                    <th className="py-1.5 text-right font-medium">Turnover</th>
                  </tr>
                </thead>
                <tbody>
                  {delivery.rows.map((r: DeliveryRow) => (
                    <tr key={r.symbol} className="border-b border-line/60">
                      <td className="py-1.5">
                        <Link
                          href={`/exchange/${r.symbol}`}
                          className="font-medium text-accent hover:underline"
                        >
                          {r.symbol}
                        </Link>
                      </td>
                      <td className="num py-1.5 text-right text-ink">
                        {r.delivery_pct?.toFixed(2)}%
                      </td>
                      <td className="num py-1.5 text-right text-ink-dim">
                        {r.close != null ? num(r.close) : "—"}
                      </td>
                      <td className={`num py-1.5 text-right ${signClass(r.change_pct)}`}>
                        {r.change_pct != null ? pct(r.change_pct) : "—"}
                      </td>
                      <td className="num py-1.5 text-right text-ink-muted">
                        {r.turnover != null ? compactInr(r.turnover) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
              {delivery.note}
            </p>
          </>
        ) : (
          <Empty message="No delivery data stored yet. Run the ingestion job to populate it." />
        )}
      </Panel>

      <Panel
        title="Disclosed deals"
        subtitle={deals?.provenance?.observed_at
          ? `Session ${String(deals.provenance.observed_at).slice(0, 10)}`
          : undefined}
        actions={
          <div className="flex gap-1">
            {(["bulk", "block"] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setDealKind(k)}
                className={`rounded px-2 py-1 text-2xs capitalize ${
                  dealKind === k
                    ? "bg-accent/10 text-accent"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                {k}
              </button>
            ))}
          </div>
        }
      >
        {deals?.error ? (
          <Unavailable reason={deals.error} />
        ) : deals?.flows?.length ? (
          <>
            <ul className="space-y-2">
              {deals.flows.slice(0, 15).map((f: DealFlow) => (
                <li key={f.symbol} className="rounded border border-line p-2.5">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <Link
                      href={`/exchange/${f.symbol}`}
                      className="text-xs font-medium text-accent hover:underline"
                    >
                      {f.symbol}
                    </Link>
                    <span className="flex items-center gap-2">
                      <span className="num text-2xs text-ink-dim">
                        {compactInr(Math.abs(f.net_value))}
                      </span>
                      <Tag tone={
                        f.direction === "NET_BUY" ? "pos"
                          : f.direction === "NET_SELL" ? "neg" : "neutral"
                      }>
                        {f.direction.replace("_", " ")}
                      </Tag>
                    </span>
                  </div>
                  <p className="mt-1 text-2xs leading-relaxed text-ink-dim">{f.note}</p>
                  {f.buyers?.length ? (
                    <p className="mt-1 text-2xs text-ink-muted">
                      Buyers: {f.buyers.slice(0, 3).join(", ")}
                    </p>
                  ) : null}
                  {f.sellers?.length ? (
                    <p className="text-2xs text-ink-muted">
                      Sellers: {f.sellers.slice(0, 3).join(", ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
              {deals.note}
            </p>
            <Provenance p={deals.provenance} />
          </>
        ) : (
          <Empty message={`No ${dealKind} deals in the published window.`} />
        )}
      </Panel>
    </div>
  );
}
