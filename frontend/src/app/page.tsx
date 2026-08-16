import Link from "next/link";
import { ResearchCard, ResearchCallCard } from "@/components/ResearchCard";
import { DataBadge, Empty, Notice, Panel, Stat, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import {
  DASH, compactNum, dateIST, dateTimeIST, inr, num, pct, signClass, titleCase,
} from "@/lib/format";

export const dynamic = "force-dynamic";

interface Overview {
  market_status: { status: string; current_time_ist: string; holiday_source: string };
  indices: {
    available: boolean;
    reason?: string;
    rows?: Array<{ symbol: string; ltp: number | null; change: number | null; change_pct: number | null; week52_high: number | null; week52_low: number | null }>;
    provenance?: { source: string; status: string; observed_at: string | null };
    note?: string;
  };
  breadth: Record<string, never> & { available: boolean; reason?: string; advances?: number; declines?: number; unchanged?: number; universe?: number; advance_decline_ratio?: number | null; new_52w_highs?: number; new_52w_lows?: number; note?: string };
  top_gainers: MoverRow[];
  top_losers: MoverRow[];
  volume_shockers: MoverRow[];
  breakouts: Array<{ symbol: string; name: string; close: number; rsi_14: number | null; volume_ratio: number | null; reason: string }>;
  breakdowns: Array<{ symbol: string; name: string; close: number; rsi_14: number | null; volume_ratio: number | null; reason: string }>;
  new_52w_highs: MoverRow[];
  new_52w_lows: MoverRow[];
  sector_performance: { available: boolean; reason?: string; rows?: Array<{ sector: string; change_pct: number | null; constituents: number | null }>; note?: string };
  flows: { available: boolean; reason?: string; fii_net?: number | null; dii_net?: number | null; as_of?: string; source?: string };
  derivatives: { available: boolean; reason?: string; options?: Array<{ underlying: string; expiry: string; pcr_oi: number | null; max_pain: number | null; underlying_value: number | null; is_demo: boolean }>; futures?: Array<{ underlying: string; expiry: string; ltp: number | null; basis_pct: number | null; buildup: string | null }> };
  ipo: { available: boolean; rows: Array<{ id: string; company: string; status: string; open_date: string | null; close_date: string | null; price_band: (number | null)[]; gmp: number | null; gmp_pct: number | null }>; gmp_notice: string };
  news: Array<{ id: string; headline: string; publisher: string; url: string; published_at: string | null; symbol: string | null; sentiment: string | null; impact_score: number | null }>;
  today: {
    corporate_actions: Array<{ symbol: string; type: string; description: string; ex_date: string | null }>;
    results: Array<{ symbol: string; quarter: string; expected_date: string | null }>;
    catalysts: Array<{ symbol: string | null; title: string; event_date: string | null; expected_impact: string | null }>;
    high_risk_events: Array<{ symbol: string | null; title: string; event_date: string | null; risk_level: string | null }>;
  };
  active_research: Array<Record<string, never>>;
  data_quality: { score: number | null; app_env: string; demo_data_visible: boolean };
}

interface MoverRow {
  symbol: string;
  name: string;
  sector: string | null;
  ltp: number | null;
  change_pct: number | null;
  volume: number | null;
  volume_ratio?: number | null;
  data_status: string;
  is_demo: boolean;
}

export default async function DashboardPage() {
  const [overviewResult, callsResult] = await Promise.all([
    apiFetch<Overview>("/api/market/overview", { auth: false }),
    apiFetch<{ calls: ResearchCallCard[] }>("/api/research/calls?limit=6", {
      auth: false,
    }),
  ]);

  if (!overviewResult.data) {
    return (
      <Panel title="Market dashboard">
        <Unavailable
          reason={overviewResult.error}
          hint={[
            "Start the backend: uvicorn app.main:app --reload (from ./backend).",
            "Check BACKEND_URL in the frontend environment.",
          ]}
        />
      </Panel>
    );
  }

  const data = overviewResult.data;
  const calls = callsResult.data?.calls ?? [];

  return (
    <div className="space-y-3">
      {data.data_quality.demo_data_visible && (
        <Notice tone="warn">
          <strong>APP_ENV = {data.data_quality.app_env}.</strong> Seeded
          demonstration rows are being served alongside any live data, and each
          one is badged <code className="text-warn">DEMO</code>. Set
          <code className="mx-1 text-warn">APP_ENV=STAGING</code> or
          <code className="mx-1 text-warn">PRODUCTION</code> to hide them
          entirely.
        </Notice>
      )}

      {/* Indices strip */}
      <Panel
        title="Index snapshot"
        actions={
          data.indices.provenance ? (
            <DataBadge
              status={data.indices.provenance.status}
              source={data.indices.provenance.source}
              observedAt={data.indices.provenance.observed_at}
            />
          ) : null
        }
      >
        {data.indices.available && data.indices.rows?.length ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
              {data.indices.rows.map((index) => (
                <div key={index.symbol} className="rounded border border-line bg-raised/40 p-2.5">
                  <div className="truncate text-2xs uppercase tracking-wide text-ink-muted">
                    {index.symbol}
                  </div>
                  <div className="num mt-0.5 text-base font-bold text-ink">
                    {num(index.ltp)}
                  </div>
                  <div className={`num text-2xs ${signClass(index.change_pct)}`}>
                    {num(index.change)} ({pct(index.change_pct)})
                  </div>
                </div>
              ))}
            </div>
            {data.indices.note && (
              <p className="mt-2 text-2xs text-ink-muted">{data.indices.note}</p>
            )}
          </>
        ) : (
          <Unavailable reason={data.indices.reason} />
        )}
      </Panel>

      <div className="grid gap-3 xl:grid-cols-3">
        {/* Breadth */}
        <Panel title="Market breadth">
          {data.breadth.available ? (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <Stat label="Advances" value={data.breadth.advances} tone="pos" />
                <Stat label="Declines" value={data.breadth.declines} tone="neg" />
                <Stat label="Unchanged" value={data.breadth.unchanged} tone="muted" />
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-raised">
                <div
                  className="h-full bg-pos"
                  style={{
                    width: `${((data.breadth.advances ?? 0) / Math.max(1, data.breadth.universe ?? 1)) * 100}%`,
                  }}
                  aria-hidden
                />
              </div>
              <div className="grid grid-cols-3 gap-2">
                <Stat label="A/D ratio" value={num(data.breadth.advance_decline_ratio)} />
                <Stat label="52w highs" value={data.breadth.new_52w_highs} tone="pos" />
                <Stat label="52w lows" value={data.breadth.new_52w_lows} tone="neg" />
              </div>
              <p className="text-2xs text-ink-muted">{data.breadth.note}</p>
            </div>
          ) : (
            <Unavailable reason={data.breadth.reason} />
          )}
        </Panel>

        {/* Sector performance */}
        <Panel title="Sector performance" subtitle={data.sector_performance.note}>
          {data.sector_performance.available && data.sector_performance.rows?.length ? (
            <ul className="space-y-1.5">
              {data.sector_performance.rows.slice(0, 10).map((row) => (
                <li key={row.sector} className="flex items-center gap-2">
                  <span className="w-32 shrink-0 truncate text-2xs text-ink-dim">
                    {row.sector}
                  </span>
                  <div className="h-3 flex-1 overflow-hidden rounded-sm bg-raised">
                    <div
                      className={(row.change_pct ?? 0) >= 0 ? "h-full bg-pos/70" : "h-full bg-neg/70"}
                      style={{
                        width: `${Math.min(100, Math.abs(row.change_pct ?? 0) * 12)}%`,
                      }}
                      aria-hidden
                    />
                  </div>
                  <span className={`num w-14 shrink-0 text-right text-2xs ${signClass(row.change_pct)}`}>
                    {pct(row.change_pct)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Unavailable reason={data.sector_performance.reason} />
          )}
        </Panel>

        {/* Flows */}
        <Panel title="FII / DII flows">
          {data.flows.available ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <Stat
                  label="FII net"
                  value={inr(data.flows.fii_net)}
                  tone={(data.flows.fii_net ?? 0) >= 0 ? "pos" : "neg"}
                />
                <Stat
                  label="DII net"
                  value={inr(data.flows.dii_net)}
                  tone={(data.flows.dii_net ?? 0) >= 0 ? "pos" : "neg"}
                />
              </div>
              <p className="text-2xs text-ink-muted">
                {dateIST(data.flows.as_of)} · {data.flows.source}
              </p>
            </div>
          ) : (
            <Unavailable reason={data.flows.reason} />
          )}
        </Panel>
      </div>

      {/* Movers */}
      <div className="grid gap-3 xl:grid-cols-3">
        <MoverPanel title="Top gainers" rows={data.top_gainers} />
        <MoverPanel title="Top losers" rows={data.top_losers} />
        <MoverPanel
          title="Volume shockers"
          rows={data.volume_shockers}
          extraColumn={{ header: "Vol ×", render: (row) => num(row.volume_ratio, 2) }}
          empty="No instrument is trading at more than twice its 20-day average volume."
        />
      </div>

      {/* Breakouts */}
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Breakouts" subtitle="Closed above the upper Bollinger band on above-average volume">
          {data.breakouts.length ? (
            <ul className="divide-y divide-line/60">
              {data.breakouts.map((row) => (
                <li key={row.symbol} className="py-2 first:pt-0 last:pb-0">
                  <Link href={`/stocks/${row.symbol}`} className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <span className="text-xs font-semibold text-ink">{row.symbol}</span>
                      <p className="truncate text-2xs text-ink-muted">{row.reason}</p>
                    </div>
                    <div className="num shrink-0 text-right text-2xs text-ink-dim">
                      RSI {num(row.rsi_14, 1)} · {num(row.volume_ratio, 2)}×
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <Empty message="No breakouts in the latest indicator snapshot." />
          )}
        </Panel>
        <Panel title="Breakdowns" subtitle="Closed below the lower Bollinger band on above-average volume">
          {data.breakdowns.length ? (
            <ul className="divide-y divide-line/60">
              {data.breakdowns.map((row) => (
                <li key={row.symbol} className="py-2 first:pt-0 last:pb-0">
                  <Link href={`/stocks/${row.symbol}`} className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <span className="text-xs font-semibold text-ink">{row.symbol}</span>
                      <p className="truncate text-2xs text-ink-muted">{row.reason}</p>
                    </div>
                    <div className="num shrink-0 text-right text-2xs text-ink-dim">
                      RSI {num(row.rsi_14, 1)} · {num(row.volume_ratio, 2)}×
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <Empty message="No breakdowns in the latest indicator snapshot." />
          )}
        </Panel>
      </div>

      {/* Research cards */}
      <Panel
        title="Active research"
        subtitle="Status is recomputed from live price on every read — the badge is never permanent"
        actions={
          <Link href="/research" className="btn px-2 py-1">
            View all
          </Link>
        }
        bodyClassName="p-3.5"
      >
        {calls.length ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {calls.map((call) => (
              <ResearchCard key={call.id} call={call} />
            ))}
          </div>
        ) : (
          <Empty message="No published research calls yet." />
        )}
      </Panel>

      <div className="grid gap-3 xl:grid-cols-3">
        {/* Derivatives */}
        <Panel title="F&O summary">
          {data.derivatives.available ? (
            <div className="space-y-3">
              {data.derivatives.options?.length ? (
                <div>
                  <h3 className="mb-1 text-2xs uppercase tracking-wide text-ink-muted">
                    Option chains
                  </h3>
                  <ul className="space-y-1">
                    {data.derivatives.options.slice(0, 5).map((row) => (
                      <li key={`${row.underlying}-${row.expiry}`} className="flex items-center justify-between text-xs">
                        <Link href={`/fno/options?symbol=${row.underlying}`} className="text-ink hover:text-accent">
                          {row.underlying}
                        </Link>
                        <span className="num text-2xs text-ink-dim">
                          PCR {num(row.pcr_oi, 2)} · max pain {num(row.max_pain, 0)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {data.derivatives.futures?.length ? (
                <div>
                  <h3 className="mb-1 text-2xs uppercase tracking-wide text-ink-muted">
                    Futures
                  </h3>
                  <ul className="space-y-1">
                    {data.derivatives.futures.slice(0, 5).map((row) => (
                      <li key={`${row.underlying}-${row.expiry}`} className="flex items-center justify-between text-xs">
                        <span className="text-ink">{row.underlying}</span>
                        <span className="num text-2xs text-ink-dim">
                          basis {pct(row.basis_pct)} · {titleCase(row.buildup)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : (
            <Unavailable reason={data.derivatives.reason} />
          )}
        </Panel>

        {/* IPO */}
        <Panel title="IPO watch" subtitle={data.ipo.gmp_notice}>
          {data.ipo.rows.length ? (
            <ul className="divide-y divide-line/60">
              {data.ipo.rows.slice(0, 5).map((row) => (
                <li key={row.id} className="py-2 first:pt-0 last:pb-0">
                  <Link href={`/ipo/${row.id}`} className="block">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-medium text-ink">
                        {row.company}
                      </span>
                      <Tag tone={row.status === "OPEN" ? "pos" : "neutral"}>
                        {row.status}
                      </Tag>
                    </div>
                    <div className="mt-0.5 flex items-center justify-between text-2xs text-ink-muted">
                      <span>
                        {inr(row.price_band[0])}–{inr(row.price_band[1])}
                      </span>
                      <span className="num">
                        GMP {row.gmp !== null ? `₹${num(row.gmp, 0)} (${pct(row.gmp_pct, 1, false)})` : DASH}
                      </span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <Empty message="No open or upcoming IPOs recorded." />
          )}
        </Panel>

        {/* Events */}
        <Panel title="This week">
          <div className="space-y-3">
            {data.today.high_risk_events.length > 0 && (
              <Notice tone="warn">
                <strong>High event risk:</strong>{" "}
                {data.today.high_risk_events
                  .slice(0, 3)
                  .map((e) => `${e.symbol ?? "Market"} — ${e.title}`)
                  .join("; ")}
              </Notice>
            )}
            <EventList title="Results" items={data.today.results.map((r) => ({ key: r.symbol + r.quarter, label: `${r.symbol} · ${r.quarter}`, date: r.expected_date }))} />
            <EventList title="Corporate actions" items={data.today.corporate_actions.map((a) => ({ key: a.symbol + a.type, label: `${a.symbol} · ${titleCase(a.type)}`, date: a.ex_date }))} />
            <EventList title="Catalysts" items={data.today.catalysts.map((c) => ({ key: (c.symbol ?? "") + c.title, label: `${c.symbol ?? "Market"} · ${c.title}`, date: c.event_date }))} />
          </div>
        </Panel>
      </div>

      {/* News */}
      <Panel
        title="Market news"
        actions={<Link href="/news" className="btn px-2 py-1">All news</Link>}
      >
        {data.news.length ? (
          <ul className="divide-y divide-line/60">
            {data.news.slice(0, 8).map((item) => (
              <li key={item.id} className="py-2 first:pt-0 last:pb-0">
                <a href={item.url} target="_blank" rel="noopener noreferrer" className="block hover:text-accent">
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0 flex-1 text-xs leading-relaxed text-ink">
                      {item.headline}
                    </p>
                    {item.impact_score !== null && (
                      <Tag
                        tone={
                          item.sentiment === "POSITIVE"
                            ? "pos"
                            : item.sentiment === "NEGATIVE"
                              ? "neg"
                              : "neutral"
                        }
                      >
                        {num(item.impact_score, 0)}
                      </Tag>
                    )}
                  </div>
                  <p className="mt-0.5 text-2xs text-ink-muted">
                    {item.publisher} · {dateTimeIST(item.published_at)}
                    {item.symbol && ` · ${item.symbol}`}
                  </p>
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <Empty message="No news has been ingested yet. Run the news_refresh job." />
        )}
      </Panel>
    </div>
  );
}

function MoverPanel({
  title,
  rows,
  extraColumn,
  empty,
}: {
  title: string;
  rows: MoverRow[];
  extraColumn?: { header: string; render: (row: MoverRow) => string };
  empty?: string;
}) {
  return (
    <Panel title={title} bodyClassName="p-0">
      {rows.length ? (
        <div className="scroll-x">
          <table className="w-full">
            <thead className="border-b border-line">
              <tr>
                <th className="th">Symbol</th>
                <th className="th text-right">LTP</th>
                <th className="th text-right">Change</th>
                {extraColumn && <th className="th text-right">{extraColumn.header}</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {rows.slice(0, 8).map((row) => (
                <tr key={row.symbol} className="hover:bg-raised/50">
                  <td className="td">
                    <Link href={`/stocks/${row.symbol}`} className="font-medium text-ink hover:text-accent">
                      {row.symbol}
                    </Link>
                    {row.is_demo && <span className="ml-1 text-[9px] text-warn">DEMO</span>}
                  </td>
                  <td className="td num text-right">{num(row.ltp)}</td>
                  <td className={`td num text-right ${signClass(row.change_pct)}`}>
                    {pct(row.change_pct)}
                  </td>
                  {extraColumn && (
                    <td className="td num text-right text-ink-dim">
                      {extraColumn.render(row)}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="p-3.5">
          <Empty message={empty || "No rows. Run the quote refresh job."} />
        </div>
      )}
    </Panel>
  );
}

function EventList({
  title,
  items,
}: {
  title: string;
  items: Array<{ key: string; label: string; date: string | null }>;
}) {
  return (
    <div>
      <h3 className="mb-1 text-2xs uppercase tracking-wide text-ink-muted">{title}</h3>
      {items.length ? (
        <ul className="space-y-0.5">
          {items.slice(0, 4).map((item) => (
            <li key={item.key} className="flex items-center justify-between gap-2 text-2xs">
              <span className="truncate text-ink-dim">{item.label}</span>
              <span className="num shrink-0 text-ink-muted">{dateIST(item.date)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-2xs text-ink-muted">Nothing scheduled in the window.</p>
      )}
    </div>
  );
}
