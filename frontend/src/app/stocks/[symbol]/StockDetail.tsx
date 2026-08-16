"use client";

import clsx from "clsx";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { EvidencePanel, WhyPanels } from "@/components/EvidencePanel";
import { ChartPayload, OverlayPicker, PriceChart } from "@/components/PriceChart";
import {
  DataBadge, Disclosure, Empty, Loading, Notice, Panel, ScoreBar, Stat, Tag,
  Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import {
  DASH, compactInr, dateIST, dateTimeIST, inr, num, pct, signClass, titleCase,
} from "@/lib/format";

const TABS = [
  "overview", "technical", "fundamentals", "research", "news",
  "documents", "corporate-actions",
] as const;
type Tab = (typeof TABS)[number];

const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"];
const DEFAULT_OVERLAYS = ["sma_20", "sma_50", "sma_200"];

export function StockDetail({
  symbol,
  overview,
  initialTab,
}: {
  symbol: string;
  overview: any;
  initialTab: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [interval, setInterval] = useState("1d");
  const [overlays, setOverlays] = useState<string[]>(DEFAULT_OVERLAYS);
  const [chart, setChart] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(true);
  const [research, setResearch] = useState<any>(null);
  const [researchLoading, setResearchLoading] = useState(false);
  const [news, setNews] = useState<any>(null);
  const [fundamentals, setFundamentals] = useState<any>(null);
  const [actions, setActions] = useState<any>(null);
  const [documents, setDocuments] = useState<any>(null);
  const [calls, setCalls] = useState<any>(null);

  const instrument = overview.instrument;
  const quote = overview.quote;

  // Chart
  useEffect(() => {
    let cancelled = false;
    setChartLoading(true);
    const indicators = [
      ...DEFAULT_OVERLAYS, "ema_9", "ema_20", "ema_50", "sma_100",
      "bb_upper", "bb_lower", "vwap", "supertrend", "rsi_14", "macd", "adx_14",
    ].join(",");
    api
      .get<ChartPayload>(
        `/api/stocks/${symbol}/chart?interval=${interval}&indicators=${indicators}`,
        false,
      )
      .then((response) => {
        if (cancelled) return;
        setChart(response.data ?? { available: false, reason: response.error, candles: [], indicators: {}, available_indicators: [] });
        setChartLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, interval]);

  // Lazy-load each tab's payload once.
  useEffect(() => {
    if (tab === "research" && !research && !researchLoading) {
      setResearchLoading(true);
      api.get(`/api/stocks/${symbol}/research?interval=${interval}`, false).then((r) => {
        setResearch(r.data ?? { error: r.error });
        setResearchLoading(false);
      });
    }
    if (tab === "news" && !news) {
      api.get(`/api/stocks/${symbol}/news`, false).then((r) => setNews(r.data ?? { error: r.error }));
    }
    if (tab === "fundamentals" && !fundamentals) {
      api.get(`/api/stocks/${symbol}/fundamentals`, false).then((r) => setFundamentals(r.data ?? { error: r.error }));
    }
    if (tab === "documents" && !documents) {
      api.get(`/api/stocks/${symbol}/documents`, false).then((r) => setDocuments(r.data ?? { error: r.error }));
    }
    if (tab === "corporate-actions" && !actions) {
      api.get(`/api/stocks/${symbol}/corporate-actions`, false).then((r) => setActions(r.data ?? { error: r.error }));
    }
    if ((tab === "overview" || tab === "research") && !calls) {
      api.get(`/api/stocks/${symbol}/calls`, false).then((r) => setCalls(r.data ?? { error: r.error }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, symbol, interval]);

  const priceLines = useMemo(() => {
    const lines: Array<{ price: number; label: string; colour: "pos" | "neg" | "accent" }> = [];
    const first = calls?.calls?.[0];
    if (first) {
      if (first.stop_loss) lines.push({ price: first.stop_loss, label: "SL", colour: "neg" });
      if (first.entry_max) lines.push({ price: first.entry_max, label: "Entry", colour: "accent" });
      if (first.targets?.length) {
        lines.push({ price: first.targets[first.targets.length - 1], label: "Target", colour: "pos" });
      }
    }
    return lines;
  }, [calls]);

  return (
    <div className="space-y-3">
      {/* header */}
      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-lg font-bold tracking-wide text-ink">
                {instrument.symbol}
              </h1>
              <Tag tone="neutral">{instrument.exchange}</Tag>
              {instrument.bse_code && <Tag tone="neutral">BSE {instrument.bse_code}</Tag>}
              {instrument.is_fno_eligible && <Tag tone="accent">F&amp;O</Tag>}
              {instrument.is_demo && <Tag tone="warn">DEMO</Tag>}
            </div>
            <p className="mt-0.5 text-xs text-ink-dim">{instrument.name}</p>
            <p className="mt-0.5 text-2xs text-ink-muted">
              {[instrument.sector, instrument.industry, instrument.isin]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>

          <div className="flex items-start gap-6">
            {quote.available ? (
              <>
                <div>
                  <div className="num text-2xl font-bold text-ink">
                    {inr(quote.ltp)}
                  </div>
                  <div className={`num text-xs ${signClass(quote.change_pct)}`}>
                    {num(quote.change)} ({pct(quote.change_pct)})
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-x-5 gap-y-1 sm:grid-cols-4">
                  <Stat label="Open" value={num(quote.open)} />
                  <Stat label="High" value={num(quote.high)} />
                  <Stat label="Low" value={num(quote.low)} />
                  <Stat label="Prev close" value={num(quote.previous_close)} />
                  <Stat label="Volume" value={quote.volume?.toLocaleString("en-IN") ?? DASH} />
                  <Stat label="VWAP" value={num(quote.vwap)} />
                  <Stat label="52w high" value={num(quote.week52_high)} />
                  <Stat label="52w low" value={num(quote.week52_low)} />
                </div>
              </>
            ) : (
              <Unavailable reason={quote.reason} />
            )}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-2.5">
          <DataBadge
            status={overview.provenance?.status}
            source={overview.provenance?.source}
            observedAt={overview.provenance?.observed_at}
          />
          <span className="text-2xs text-ink-muted">
            Market {overview.market_status.status.replace("_", " ").toLowerCase()} ·{" "}
            {overview.market_status.timezone}
          </span>
          <div className="ml-auto flex gap-1.5">
            <Link href={`/alerts?symbol=${symbol}`} className="btn">Create alert</Link>
            <Link href={`/watchlist?add=${symbol}`} className="btn">Watchlist</Link>
            {instrument.is_fno_eligible && (
              <Link href={`/fno/options?symbol=${symbol}`} className="btn">Options</Link>
            )}
          </div>
        </div>
      </Panel>

      {/* tabs */}
      <div className="scroll-x border-b border-line">
        <div role="tablist" className="flex gap-1">
          {TABS.map((key) => (
            <button
              key={key}
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={clsx(
                "whitespace-nowrap border-b-2 px-3 py-2 text-xs font-medium transition-colors",
                tab === key
                  ? "border-accent text-accent"
                  : "border-transparent text-ink-muted hover:text-ink",
              )}
            >
              {titleCase(key)}
            </button>
          ))}
        </div>
      </div>

      {tab === "overview" && (
        <div className="space-y-3">
          <Panel
            title="Price chart"
            actions={
              <div className="flex items-center gap-1">
                {INTERVALS.map((value) => (
                  <button
                    key={value}
                    onClick={() => setInterval(value)}
                    className={clsx(
                      "rounded px-1.5 py-0.5 text-2xs font-medium",
                      interval === value
                        ? "bg-accent/15 text-accent"
                        : "text-ink-muted hover:text-ink",
                    )}
                  >
                    {value}
                  </button>
                ))}
              </div>
            }
          >
            <div className="mb-2">
              <OverlayPicker
                available={chart?.available_indicators ?? []}
                selected={overlays}
                onChange={setOverlays}
              />
            </div>
            <PriceChart
              data={chart}
              overlays={overlays}
              priceLines={priceLines}
              loading={chartLoading}
            />
          </Panel>

          {overview.key_ratios?.available !== false && (
            <Panel title="Key ratios">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
                <Stat label="P/E" value={num(overview.key_ratios.pe, 1)} />
                <Stat label="P/B" value={num(overview.key_ratios.pb, 2)} />
                <Stat label="EPS (TTM)" value={num(overview.key_ratios.eps_ttm, 2)} />
                <Stat label="ROE" value={pct(overview.key_ratios.roe, 1, false)} />
                <Stat label="ROCE" value={pct(overview.key_ratios.roce, 1, false)} />
                <Stat label="Debt/Equity" value={num(overview.key_ratios.debt_to_equity, 2)} />
                <Stat label="Dividend yield" value={pct(overview.key_ratios.dividend_yield, 2, false)} />
                <Stat label="Beta" value={num(overview.key_ratios.beta, 2)} />
                <Stat label="Promoter" value={pct(overview.key_ratios.promoter_holding, 2, false)} />
                <Stat label="FII" value={pct(overview.key_ratios.fii_holding, 2, false)} />
                <Stat label="DII" value={pct(overview.key_ratios.dii_holding, 2, false)} />
                <Stat label="Market cap" value={compactInr(quote.market_cap)} />
              </div>
            </Panel>
          )}

          {calls?.calls?.length > 0 && (
            <Panel title="Research calls on this instrument">
              <ul className="space-y-2">
                {calls.calls.map((call: any) => (
                  <li key={call.id} className="rounded border border-line bg-raised/30 p-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Tag tone={call.side === "BUY" ? "pos" : "neg"}>{call.side}</Tag>
                        <span className="text-xs text-ink">{call.status_reason}</span>
                      </div>
                      <Link href={`/research/${call.id}`} className="btn px-2 py-1">
                        Evidence
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      )}

      {tab === "technical" && (
        <TechnicalTab symbol={symbol} interval={interval} />
      )}

      {tab === "fundamentals" && (
        <FundamentalsTab data={fundamentals} />
      )}

      {tab === "research" && (
        <ResearchTab data={research} loading={researchLoading} />
      )}

      {tab === "news" && <NewsTab data={news} />}

      {tab === "documents" && <DocumentsTab data={documents} />}

      {tab === "corporate-actions" && <ActionsTab data={actions} />}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function TechnicalTab({ symbol, interval }: { symbol: string; interval: string }) {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api.get(`/api/stocks/${symbol}/technicals?interval=${interval}`, false)
      .then((r) => setData(r.data ?? { error: r.error }));
  }, [symbol, interval]);

  if (!data) return <Loading label="Computing indicators" />;
  if (data.error) return <Unavailable reason={data.error} />;

  return (
    <div className="space-y-3">
      <Panel title="Technical reading" subtitle={data.explanation}>
        <EvidencePanel chain={data.evidence_chain} defaultOpen />
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Indicator snapshot">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {Object.entries(data.indicators || {})
              .filter(([, value]) => value !== null)
              .map(([key, value]) => (
                <Stat key={key} label={key} value={num(value as number, 2)} />
              ))}
          </div>
        </Panel>

        <Panel title="Support and resistance" subtitle="Clustered from confirmed swing points">
          {data.levels?.length ? (
            <ul className="space-y-1.5">
              {data.levels.map((level: any) => (
                <li key={level.price} className="flex items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-2">
                    <Tag tone={level.kind === "SUPPORT" ? "pos" : "neg"}>{level.kind}</Tag>
                    <span className="num font-medium text-ink">{inr(level.price)}</span>
                  </div>
                  <span className="text-2xs text-ink-muted">
                    {level.touches} touches · strength {level.strength}/100 ·{" "}
                    {pct(level.distance_pct)} away
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty message="No confirmed swing levels in the window." />
          )}
        </Panel>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Market regime">
          <div className="space-y-2">
            <Tag tone="accent">{data.regime?.regime}</Tag>
            <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-dim">
              {(data.regime?.reasons || []).map((reason: string) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        </Panel>
        <Panel title="Gap and divergence">
          <div className="space-y-2 text-xs">
            {data.gap ? (
              <p className="text-ink-dim">
                Latest gap: <strong className="text-ink">{data.gap.type}</strong>{" "}
                ({pct(data.gap.gap_pct)}), from {inr(data.gap.previous_close)} to{" "}
                {inr(data.gap.open)}
                {data.gap.filled !== null && (data.gap.filled ? " — filled." : " — not filled.")}
              </p>
            ) : (
              <p className="text-ink-muted">No gap data available.</p>
            )}
            {data.divergence ? (
              <p className="text-ink-dim">
                <strong className="text-ink">{data.divergence.type}</strong> —{" "}
                {data.divergence.note}
              </p>
            ) : (
              <p className="text-ink-muted">No RSI divergence detected in the window.</p>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function FundamentalsTab({ data }: { data: any }) {
  if (!data) return <Loading label="Loading fundamentals" />;
  if (data.error) return <Unavailable reason={data.error} />;
  const fundamental = data.fundamental;
  if (!fundamental?.available) {
    return <Unavailable reason={fundamental?.reason} />;
  }
  const quality = fundamental.quality_score;

  return (
    <div className="space-y-3">
      <Panel
        title="Company quality score"
        subtitle={quality?.explanation}
        actions={
          <span className="num text-lg font-bold text-accent">
            {num(quality?.total, 1)}
            <span className="text-xs text-ink-muted">/100</span>
          </span>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(quality?.categories || {}).map(([key, value]: [string, any]) => (
            <ScoreBar
              key={key}
              label={titleCase(key)}
              score={value.score_pct}
              note={`${value.earned_points}/${value.available_points} points · coverage ${value.coverage_pct}%`}
            />
          ))}
        </div>

        <Disclosure summary="Why this score? — every metric, input, weight and source" count={quality?.metrics?.length}>
          <div className="scroll-x">
            <table className="w-full min-w-[820px]">
              <thead>
                <tr>
                  <th className="th">Metric</th>
                  <th className="th">Category</th>
                  <th className="th text-right">Value</th>
                  <th className="th">Band</th>
                  <th className="th text-right">Points</th>
                  <th className="th">Calculation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {(quality?.metrics || []).map((metric: any) => (
                  <tr key={metric.key} className={metric.value === null ? "opacity-50" : ""}>
                    <td className="td text-ink">{metric.label}</td>
                    <td className="td text-ink-muted">{titleCase(metric.category)}</td>
                    <td className="td num text-right">
                      {metric.value === null ? "n/a" : `${num(metric.value, 2)}${metric.unit === "%" ? "%" : ""}`}
                    </td>
                    <td className="td num text-ink-muted">{metric.band || "—"}</td>
                    <td className="td num text-right">
                      {metric.points === null ? "—" : `${num(metric.points, 2)}/${num(metric.max_points, 2)}`}
                    </td>
                    <td className="td max-w-[280px] truncate text-2xs text-ink-muted" title={metric.calculation}>
                      {metric.calculation}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {quality?.missing?.length > 0 && (
            <Notice tone="warn">
              Not available: {quality.missing.join(", ")}. Missing metrics are
              excluded from both the numerator and the denominator — they are not
              scored as a pass.
            </Notice>
          )}
        </Disclosure>
      </Panel>

      <Panel title="Fundamental evidence">
        <EvidencePanel chain={fundamental.evidence_chain} />
      </Panel>

      {data.statements?.length > 0 && (
        <Panel title="Financial statements" subtitle="As reported by the configured source">
          <div className="scroll-x">
            <table className="w-full min-w-[900px]">
              <thead>
                <tr>
                  <th className="th">Period</th>
                  <th className="th text-right">Revenue</th>
                  <th className="th text-right">EBITDA</th>
                  <th className="th text-right">Margin</th>
                  <th className="th text-right">PAT</th>
                  <th className="th text-right">EPS</th>
                  <th className="th text-right">OCF</th>
                  <th className="th text-right">FCF</th>
                  <th className="th text-right">Debt</th>
                  <th className="th">Source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {data.statements.map((row: any) => (
                  <tr key={row.period_label + row.period_end}>
                    <td className="td font-medium text-ink">{row.period_label}</td>
                    <td className="td num text-right">{compactInr(row.revenue)}</td>
                    <td className="td num text-right">{compactInr(row.ebitda)}</td>
                    <td className="td num text-right">{pct(row.ebitda_margin, 1, false)}</td>
                    <td className="td num text-right">{compactInr(row.pat)}</td>
                    <td className="td num text-right">{num(row.eps, 2)}</td>
                    <td className="td num text-right">{compactInr(row.operating_cash_flow)}</td>
                    <td className="td num text-right">{compactInr(row.free_cash_flow)}</td>
                    <td className="td num text-right">{compactInr(row.total_debt)}</td>
                    <td className="td text-2xs text-ink-muted">
                      {row.source}
                      {row.is_demo && <span className="ml-1 text-warn">DEMO</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {data.shareholding?.length > 0 && (
        <Panel title="Shareholding pattern">
          <div className="scroll-x">
            <table className="w-full min-w-[620px]">
              <thead>
                <tr>
                  <th className="th">As of</th>
                  <th className="th text-right">Promoter</th>
                  <th className="th text-right">Pledged</th>
                  <th className="th text-right">FII</th>
                  <th className="th text-right">DII</th>
                  <th className="th text-right">Public</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {data.shareholding.map((row: any) => (
                  <tr key={row.as_of}>
                    <td className="td">{dateIST(row.as_of)}</td>
                    <td className="td num text-right">{pct(row.promoter, 2, false)}</td>
                    <td className="td num text-right">{pct(row.promoter_pledged, 2, false)}</td>
                    <td className="td num text-right">{pct(row.fii, 2, false)}</td>
                    <td className="td num text-right">{pct(row.dii, 2, false)}</td>
                    <td className="td num text-right">{pct(row.public, 2, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {fundamental.peer_context && Object.keys(fundamental.peer_context).length > 0 && (
        <Panel title="Peer comparison">
          <div className="scroll-x">
            <table className="w-full min-w-[560px]">
              <thead>
                <tr>
                  <th className="th">Metric</th>
                  <th className="th text-right">This company</th>
                  <th className="th text-right">Peer median</th>
                  <th className="th text-right">Peer range</th>
                  <th className="th text-right">Peers</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {Object.entries(fundamental.peer_context).map(([metric, value]: [string, any]) => (
                  <tr key={metric}>
                    <td className="td uppercase text-ink">{metric}</td>
                    <td className="td num text-right">{num(value.value, 2)}</td>
                    <td className="td num text-right text-ink-dim">{num(value.peer_median, 2)}</td>
                    <td className="td num text-right text-ink-muted">
                      {num(value.peer_min, 2)} – {num(value.peer_max, 2)}
                    </td>
                    <td className="td num text-right text-ink-muted">{value.peer_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

function ResearchTab({ data, loading }: { data: any; loading: boolean }) {
  if (loading || !data) return <Loading label="Assembling the evidence chain" />;
  if (data.error) return <Unavailable reason={data.error} />;

  const setup = data.trade_setup;

  return (
    <div className="space-y-3">
      {data.warnings?.length > 0 && (
        <Notice tone="warn">
          <ul className="list-inside list-disc space-y-0.5">
            {data.warnings.map((warning: string) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      )}

      <Panel
        title="Research scorecard"
        subtitle={data.confidence?.explanation}
        actions={
          <div className="text-right">
            <div className="num text-lg font-bold text-accent">
              {num(data.confidence?.overall, 0)}
              <span className="text-xs text-ink-muted">/100</span>
            </div>
            <Tag tone="neutral">{data.confidence?.state?.replace(/_/g, " ")}</Tag>
          </div>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {Object.entries(data.scorecard || {})
            .filter(([key, value]: [string, any]) => typeof value === "object" && value !== null)
            .map(([key, value]: [string, any]) => (
              <ScoreBar
                key={key}
                label={titleCase(key)}
                score={value.score}
                note={value.note}
                inverted={Boolean(value.inverted)}
              />
            ))}
        </div>
        <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
          {data.confidence?.caveat}
        </p>
      </Panel>

      {data.confidence?.conflict?.conflict_detected && (
        <Notice tone="warn">
          <strong>Evidence conflict detected.</strong>{" "}
          {data.confidence.conflict.message} The platform will not manufacture a
          direction when the dimensions disagree.
        </Notice>
      )}

      <WhyPanels whyNow={data.why_now || []} whyNot={data.why_not || []} />

      <Panel title="Trade setup" subtitle="A calculated analytical scenario — not a recommendation">
        {setup?.available ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              <Stat label="Direction" value={setup.direction} />
              <Stat label="Entry zone" value={`${num(setup.entry_zone[0])} – ${num(setup.entry_zone[1])}`} />
              <Stat label="Stop loss" value={inr(setup.stop_loss)} tone="neg" />
              <Stat label="Risk/reward" value={setup.risk_reward ? `1 : ${num(setup.risk_reward, 2)}` : DASH} />
              <Stat label="Risk rating" value={setup.risk_rating} tone="warn" />
              <Stat label="Confidence" value={`${num(setup.confidence, 0)}/100`} />
            </div>

            <div className="scroll-x">
              <table className="w-full min-w-[520px]">
                <thead>
                  <tr>
                    <th className="th">Target</th>
                    <th className="th text-right">Price</th>
                    <th className="th text-right">From entry</th>
                    <th className="th text-right">From LTP</th>
                    <th className="th text-right">R multiple</th>
                    <th className="th">Reached</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {(setup.targets || []).map((target: any) => (
                    <tr key={target.index}>
                      <td className="td">T{target.index}</td>
                      <td className="td num text-right">{inr(target.price)}</td>
                      <td className="td num text-right text-pos">{pct(target.return_from_entry_pct)}</td>
                      <td className="td num text-right">{pct(target.return_from_ltp_pct)}</td>
                      <td className="td num text-right">{target.r_multiple ? `${num(target.r_multiple, 2)}R` : DASH}</td>
                      <td className="td">{target.reached ? "Yes" : "No"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="rounded border border-line bg-raised/40 p-2.5">
              <h4 className="text-2xs uppercase tracking-wide text-ink-muted">
                How the levels were derived
              </h4>
              <dl className="mt-1 grid gap-x-4 gap-y-0.5 text-2xs text-ink-dim sm:grid-cols-2">
                {Object.entries(setup.sizing_basis || {}).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2">
                    <dt className="text-ink-muted">{titleCase(key)}</dt>
                    <dd className="num text-ink">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <div>
              <h4 className="mb-1 text-2xs uppercase tracking-wide text-neg">
                What would invalidate this
              </h4>
              <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-dim">
                {(setup.invalidation || []).map((line: string) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>

            <Notice tone="info">{setup.disclaimer}</Notice>
          </div>
        ) : (
          <div className="space-y-2">
            <Notice tone="warn">
              <strong>No setup generated.</strong> {setup?.reason}
              {setup?.detail && <> {setup.detail}</>}
            </Notice>
            {setup?.note && <p className="text-2xs text-ink-muted">{setup.note}</p>}
          </div>
        )}
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Technical evidence">
          <EvidencePanel chain={data.technical?.evidence_chain} />
        </Panel>
        <Panel title="Options evidence">
          {data.options ? (
            <EvidencePanel chain={data.options.evidence_chain} />
          ) : (
            <Empty message="No option chain is available for this instrument." />
          )}
        </Panel>
      </div>

      <Panel
        title="Historical analogues"
        subtitle="What followed similar past configurations in this instrument's own history"
      >
        {data.historical_analogues?.sample_sufficient ? (
          <div className="space-y-3">
            <p className="text-xs leading-relaxed text-ink-dim">
              {data.historical_analogues.explanation}
            </p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              {Object.entries(data.historical_analogues.statistics)
                .filter(([, value]) => typeof value === "number")
                .map(([key, value]) => (
                  <Stat key={key} label={titleCase(key)} value={num(value as number, 2)} />
                ))}
            </div>
            <Notice tone="warn">{data.historical_analogues.disclaimer}</Notice>
            <Disclosure summary="Limitations of this sample" count={data.historical_analogues.limitations?.length}>
              <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
                {data.historical_analogues.limitations.map((limit: string) => (
                  <li key={limit}>{limit}</li>
                ))}
              </ul>
            </Disclosure>
          </div>
        ) : (
          <Unavailable
            reason={data.historical_analogues?.explanation}
            hint={data.historical_analogues?.limitations?.slice(0, 3)}
          />
        )}
      </Panel>

      <Panel title="Risk assessment" subtitle={data.risk?.explanation}>
        <div className="space-y-2">
          <Tag tone={data.risk?.rating === "LOW" ? "pos" : data.risk?.rating === "MODERATE" ? "warn" : "neg"}>
            {data.risk?.rating} · {num(data.risk?.score, 1)}/100
          </Tag>
          <div className="scroll-x">
            <table className="w-full min-w-[640px]">
              <thead>
                <tr>
                  <th className="th">Factor</th>
                  <th className="th text-right">Score</th>
                  <th className="th text-right">Weight</th>
                  <th className="th">Explanation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {(data.risk?.factors || []).map((factor: any) => (
                  <tr key={factor.key}>
                    <td className="td text-ink">{factor.label}</td>
                    <td className="td num text-right">{num(factor.score, 0)}</td>
                    <td className="td num text-right text-ink-muted">{factor.weight}</td>
                    <td className="td max-w-[420px] whitespace-normal text-2xs text-ink-dim">
                      {factor.explanation}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.risk?.unassessed?.length > 0 && (
            <p className="text-2xs text-warn">
              Not assessed: {data.risk.unassessed.join(", ")}.
            </p>
          )}
        </div>
      </Panel>

      <Panel title="Sources" subtitle="Every input behind this page">
        <div className="scroll-x">
          <table className="w-full min-w-[720px]">
            <thead>
              <tr>
                <th className="th">Source</th>
                <th className="th">Provider</th>
                <th className="th">Status</th>
                <th className="th">Reliability</th>
                <th className="th">Observed</th>
                <th className="th">Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {(data.sources || []).map((source: any, index: number) => (
                <tr key={`${source.source}-${index}`}>
                  <td className="td text-ink">{source.source}</td>
                  <td className="td text-ink-muted">{source.provider}</td>
                  <td className="td"><DataBadge status={source.status} compact /></td>
                  <td className="td text-ink-muted">{source.reliability}</td>
                  <td className="td text-ink-muted">{dateTimeIST(source.observed_at)}</td>
                  <td className="td max-w-[320px] whitespace-normal text-2xs text-ink-muted">
                    {source.notes}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-2xs text-ink-muted">{data.disclaimer}</p>
      </Panel>
    </div>
  );
}

function NewsTab({ data }: { data: any }) {
  if (!data) return <Loading label="Loading news" />;
  if (data.error) return <Unavailable reason={data.error} />;
  if (!data.available) return <Unavailable reason={data.reason} />;

  return (
    <Panel title="News with impact scoring" subtitle={data.provenance?.notes}>
      <ul className="divide-y divide-line/60">
        {data.articles.map((article: any, index: number) => (
          <li key={`${article.url}-${index}`} className="py-3 first:pt-0">
            <div className="flex items-start justify-between gap-3">
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className="min-w-0 flex-1 text-xs font-medium leading-relaxed text-ink hover:text-accent"
              >
                {article.headline}
              </a>
              <div className="flex shrink-0 items-center gap-1.5">
                <Tag tone={article.sentiment === "POSITIVE" ? "pos" : article.sentiment === "NEGATIVE" ? "neg" : "neutral"}>
                  {article.sentiment}
                </Tag>
                <Tag tone="accent">{num(article.impact_score, 0)}</Tag>
              </div>
            </div>
            <p className="mt-0.5 text-2xs text-ink-muted">
              {article.publisher} · {dateTimeIST(article.published_at)} ·{" "}
              {titleCase(article.event_category)}
            </p>
            <Disclosure summary="How this score was built">
              <p className="text-2xs leading-relaxed text-ink-dim">{article.explanation}</p>
              <dl className="mt-2 grid gap-x-4 gap-y-0.5 text-2xs sm:grid-cols-2">
                {Object.entries(article.components || {}).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2">
                    <dt className="text-ink-muted">{titleCase(key)}</dt>
                    <dd className="num text-ink">{value === null ? "n/a" : String(value)}</dd>
                  </div>
                ))}
              </dl>
              <ul className="mt-2 list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
                {(article.limitations || []).map((limit: string) => (
                  <li key={limit}>{limit}</li>
                ))}
              </ul>
            </Disclosure>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ActionsTab({ data }: { data: any }) {
  if (!data) return <Loading label="Loading corporate actions" />;
  if (data.error) return <Unavailable reason={data.error} />;

  return (
    <div className="space-y-3">
      <Panel title="Corporate actions" bodyClassName="p-0">
        {data.corporate_actions?.length ? (
          <div className="scroll-x">
            <table className="w-full min-w-[760px]">
              <thead>
                <tr>
                  <th className="th">Type</th>
                  <th className="th">Description</th>
                  <th className="th">Ex date</th>
                  <th className="th">Record date</th>
                  <th className="th text-right">Value</th>
                  <th className="th text-right">Adj factor</th>
                  <th className="th">Source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {data.corporate_actions.map((row: any, index: number) => (
                  <tr key={index}>
                    <td className="td"><Tag tone="neutral">{titleCase(row.type)}</Tag></td>
                    <td className="td max-w-[280px] truncate text-ink-dim">{row.description}</td>
                    <td className="td">{dateIST(row.ex_date)}</td>
                    <td className="td">{dateIST(row.record_date)}</td>
                    <td className="td num text-right">{num(row.value, 2)}</td>
                    <td className="td num text-right text-ink-muted">
                      {row.price_adjustment_factor ? num(row.price_adjustment_factor, 4) : "—"}
                    </td>
                    <td className="td text-2xs text-ink-muted">{row.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4"><Empty message="No corporate actions recorded." /></div>
        )}
      </Panel>

      <Panel title="Results history" bodyClassName="p-0">
        {data.results?.length ? (
          <div className="scroll-x">
            <table className="w-full min-w-[860px]">
              <thead>
                <tr>
                  <th className="th">Quarter</th>
                  <th className="th">Expected</th>
                  <th className="th">Reported</th>
                  <th className="th text-right">Revenue</th>
                  <th className="th text-right">PAT</th>
                  <th className="th text-right">EPS</th>
                  <th className="th text-right">YoY PAT</th>
                  <th className="th text-right">1d reaction</th>
                  <th className="th">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {data.results.map((row: any, index: number) => (
                  <tr key={index}>
                    <td className="td font-medium text-ink">{row.quarter}</td>
                    <td className="td">{dateIST(row.expected_date)}</td>
                    <td className="td">{dateIST(row.reported_date)}</td>
                    <td className="td num text-right">{compactInr(row.revenue)}</td>
                    <td className="td num text-right">{compactInr(row.pat)}</td>
                    <td className="td num text-right">{num(row.eps, 2)}</td>
                    <td className={`td num text-right ${signClass(row.pat_yoy_pct)}`}>
                      {pct(row.pat_yoy_pct)}
                    </td>
                    <td className={`td num text-right ${signClass(row.price_reaction_1d_pct)}`}>
                      {pct(row.price_reaction_1d_pct)}
                    </td>
                    <td className="td text-2xs text-ink-muted">{row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4"><Empty message="No results recorded." /></div>
        )}
      </Panel>
    </div>
  );
}

function DocumentsTab({ data }: { data: any }) {
  if (!data) return <Loading label="Loading filings" />;
  if (data.error) return <Unavailable reason={data.error} />;

  const claims = data.approved_claims || [];
  const figures = claims.filter((c: any) => c.type === "FIGURE");
  const risks = claims.filter((c: any) => c.type === "RISK_FACTOR");
  const commentary = claims.filter((c: any) => c.type === "COMMENTARY");

  return (
    <div className="space-y-3">
      <Panel title="Filings on file" subtitle={data.note}>
        {data.documents?.length ? (
          <ul className="divide-y divide-line/60">
            {data.documents.map((document: any) => (
              <li key={document.id} className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <p className="truncate text-xs text-ink">{document.title}</p>
                  <p className="text-2xs text-ink-muted">
                    {titleCase(document.doc_type)} · {dateIST(document.document_date)}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Tag tone={document.extraction_status === "EXTRACTED" ? "pos" : "neutral"}>
                    {titleCase(document.extraction_status)}
                  </Tag>
                  {document.url && (
                    <a
                      href={document.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-2xs text-accent underline underline-offset-2"
                    >
                      Open
                    </a>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty message="No filings registered for this company yet." />
        )}
      </Panel>

      {claims.length === 0 ? (
        <Panel title="Cited findings">
          <Notice tone="warn">
            No approved findings for this company.
            {data.pending_claims > 0
              ? ` ${data.pending_claims} extracted claim(s) are awaiting review and are deliberately excluded until a reviewer approves them.`
              : " Upload a filing and run extraction from the Documents page."}
          </Notice>
        </Panel>
      ) : (
        <>
          {figures.length > 0 && (
            <Panel
              title="Figures extracted from filings"
              subtitle="Each figure links to the page and the exact line it came from"
              bodyClassName="p-0"
            >
              <div className="scroll-x">
                <table className="w-full min-w-[900px]">
                  <thead className="border-b border-line">
                    <tr>
                      <th className="th">Metric</th>
                      <th className="th">Period</th>
                      <th className="th text-right">As printed</th>
                      <th className="th">Unit</th>
                      <th className="th text-right">Normalised</th>
                      <th className="th">Source</th>
                      <th className="th">Reviewed by</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/50">
                    {figures.map((claim: any) => (
                      <tr key={claim.id} className="hover:bg-raised/40">
                        <td className="td text-ink">{titleCase(claim.metric_key)}</td>
                        <td className="td num">{claim.period_label || DASH}</td>
                        <td className="td num text-right">{num(claim.raw_value, 2)}</td>
                        <td className="td text-2xs text-ink-muted">{claim.unit || DASH}</td>
                        <td className="td num text-right text-accent">
                          {claim.normalised_value !== null
                            ? compactInr(claim.normalised_value)
                            : DASH}
                        </td>
                        <td className="td max-w-[240px] text-2xs text-ink-muted">
                          {claim.source.title}
                          {claim.page ? `, p.${claim.page}` : ""}
                          {claim.source.url && (
                            <a
                              href={claim.source.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="ml-1 text-accent underline"
                            >
                              open
                            </a>
                          )}
                          <p className="num mt-0.5 truncate text-ink-muted" title={claim.quote}>
                            “{claim.quote}”
                          </p>
                        </td>
                        <td className="td text-2xs text-ink-muted">
                          {claim.reviewed_by || DASH}
                          <div>{dateTimeIST(claim.reviewed_at)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {risks.length > 0 && (
            <Panel title="Risk factors disclosed in filings">
              <ul className="space-y-2">
                {risks.map((claim: any) => (
                  <li key={claim.id} className="rounded border border-line bg-raised/30 p-2.5">
                    <div className="flex items-center gap-2">
                      <Tag tone="neg">{titleCase(claim.metric_key)}</Tag>
                      {claim.raw_value !== null && (
                        <span className="num text-2xs text-ink-dim">
                          {num(claim.raw_value, 1)}{claim.unit}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-ink-dim">
                      “{claim.quote}”
                    </p>
                    <p className="mt-0.5 text-2xs text-ink-muted">
                      {claim.source.title}, page {claim.page} · approved by{" "}
                      {claim.reviewed_by}
                    </p>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {commentary.length > 0 && (
            <Panel
              title="Management commentary"
              subtitle="Reproduced verbatim — the platform does not paraphrase or score it"
            >
              <ul className="space-y-2">
                {commentary.map((claim: any) => (
                  <li key={claim.id} className="rounded border border-line bg-raised/30 p-2.5">
                    <Tag tone="neutral">{claim.claim}</Tag>
                    <p className="mt-1 text-xs leading-relaxed text-ink-dim">
                      “{claim.quote}”
                    </p>
                    <p className="mt-0.5 text-2xs text-ink-muted">
                      {claim.source.title}, page {claim.page}
                    </p>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
