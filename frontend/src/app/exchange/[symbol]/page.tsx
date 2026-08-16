"use client";

/**
 * Per-symbol data integrity: what every source says, and whether they agree.
 *
 * This is the page to open before putting a number into your own research. It
 * does not show "the price" - it shows every source's price side by side, the
 * spread between them, and a consensus only where they actually agree.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Disclosure, Empty, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { compactInr, dateTimeIST, num, pct, relativeAge, signClass } from "@/lib/format";

interface Reading {
  provider: string;
  source: string;
  value: number | null;
  status: string;
  reliability: string;
  observed_at: string | null;
  age_seconds: number | null;
  error: string | null;
  deviation_pct: number | null;
  is_outlier: boolean;
  authority_rank: number;
}

interface Check {
  metric: string;
  agreement: string;
  consensus: number | null;
  authoritative_value: number | null;
  authoritative_source: string | null;
  spread_pct: number | null;
  tolerance_pct: number;
  source_count: number;
  explanation: string;
  is_trustworthy: boolean;
  readings: Reading[];
}

const AGREEMENT_TONE: Record<string, "pos" | "neg" | "warn" | "neutral"> = {
  CONFIRMED: "pos",
  MINOR_DIVERGENCE: "warn",
  CONFLICT: "neg",
  SINGLE_SOURCE: "warn",
  UNAVAILABLE: "neutral",
};

export default function SymbolIntegrityPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = String(params?.symbol ?? "").toUpperCase();

  const [verify, setVerify] = useState<any>(null);
  const [delivery, setDelivery] = useState<any>(null);
  const [eod, setEod] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!symbol) return;
    setLoading(true);
    Promise.all([
      api.get<any>(`/api/exchange/verify/${symbol}`, false),
      api.get<any>(`/api/exchange/delivery/${symbol}`, false),
      api.get<any>(`/api/exchange/eod/${symbol}?days=30`, false),
    ]).then(([v, d, e]) => {
      setVerify(v.data ?? { error: v.error });
      setDelivery(d.data ?? { error: d.error });
      setEod(e.data ?? { error: e.error });
      setLoading(false);
    });
  }, [symbol]);

  const checks: Record<string, Check> = verify?.checks ?? {};

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">
            {symbol} · data integrity
          </h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-dim">
            Every source asked independently, then compared. A consensus is
            published only where sources agree within tolerance — never by
            averaging a disagreement away.
          </p>
        </div>
        <Link
          href={`/stocks/${symbol}`}
          className="text-2xs text-accent hover:underline"
        >
          Full analysis for {symbol} →
        </Link>
      </header>

      {loading ? <Loading label={`Cross-checking ${symbol}`} /> : null}

      {verify?.verdict ? (
        <Notice tone={verify.fields_needing_attention?.length ? "warn" : "info"}>
          {verify.verdict}
        </Notice>
      ) : null}

      <Panel title="Source comparison" subtitle="One row per source, per field">
        {verify?.error ? (
          <Unavailable reason={verify.error} />
        ) : Object.keys(checks).length ? (
          <div className="space-y-3">
            {Object.entries(checks).map(([field, check]) => (
              <div key={field} className="rounded border border-line p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-xs font-medium text-ink">
                    {field.replace(/_/g, " ")}
                  </span>
                  <span className="flex items-center gap-2">
                    {check.spread_pct != null ? (
                      <span className="num text-2xs text-ink-muted">
                        spread {check.spread_pct}% / tol {check.tolerance_pct}%
                      </span>
                    ) : null}
                    <Tag tone={AGREEMENT_TONE[check.agreement] ?? "neutral"}>
                      {check.agreement.replace(/_/g, " ")}
                    </Tag>
                  </span>
                </div>

                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-2xs">
                    <thead>
                      <tr className="border-b border-line text-ink-muted">
                        <th className="py-1 text-left font-medium">Source</th>
                        <th className="py-1 text-right font-medium">Value</th>
                        <th className="py-1 text-right font-medium">Deviation</th>
                        <th className="py-1 text-right font-medium">Status</th>
                        <th className="py-1 text-right font-medium">Age</th>
                      </tr>
                    </thead>
                    <tbody>
                      {check.readings.map((r) => (
                        <tr
                          key={`${field}-${r.provider}`}
                          className="border-b border-line/50"
                        >
                          <td className="py-1 text-ink-dim">
                            {r.provider}
                            {r.is_outlier ? (
                              <span className="ml-1 text-neg">· adrift</span>
                            ) : null}
                          </td>
                          <td className="num py-1 text-right text-ink">
                            {r.value != null ? num(r.value) : "—"}
                          </td>
                          <td className={`num py-1 text-right ${signClass(r.deviation_pct)}`}>
                            {r.deviation_pct != null ? `${r.deviation_pct}%` : "—"}
                          </td>
                          <td className="py-1 text-right text-ink-muted">{r.status}</td>
                          <td className="py-1 text-right text-ink-muted">
                            {r.observed_at ? relativeAge(r.observed_at) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {check.readings.some((r) => r.error) ? (
                  <ul className="mt-1.5 space-y-0.5">
                    {check.readings.filter((r) => r.error).map((r) => (
                      <li key={`${field}-${r.provider}-err`} className="text-2xs text-ink-muted">
                        {r.provider}: {r.error}
                      </li>
                    ))}
                  </ul>
                ) : null}

                <p className="mt-2 text-2xs leading-relaxed text-ink-dim">
                  {check.explanation}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <Empty message="No sources could be reached for this symbol." />
        )}
      </Panel>

      <Panel
        title="Delivery"
        subtitle={delivery?.delivery?.session_date
          ? `Session ${delivery.delivery.session_date}`
          : undefined}
      >
        {delivery?.error ? (
          <Unavailable reason={delivery.error} />
        ) : delivery?.delivery ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat
                label="Delivery"
                value={delivery.delivery.delivery_pct != null
                  ? `${delivery.delivery.delivery_pct}%` : "—"}
              />
              <Stat
                label="Regime"
                value={delivery.delivery.regime}
                tone={delivery.delivery.regime === "ACCUMULATION" ? "pos"
                  : delivery.delivery.regime === "CHURN" ? "neg" : "muted"}
              />
              <Stat
                label="Own median"
                value={delivery.delivery.own_median_pct != null
                  ? `${delivery.delivery.own_median_pct}%` : "—"}
                hint="Median delivery for this stock across stored sessions."
              />
              <Stat
                label="Market median"
                value={delivery.delivery.market_median_pct != null
                  ? `${delivery.delivery.market_median_pct}%` : "—"}
              />
            </div>
            <p className="mt-3 text-xs leading-relaxed text-ink-dim">
              {delivery.delivery.interpretation}
            </p>
            <p className="mt-2 text-2xs text-ink-muted">
              {delivery.history_sessions_stored} prior session(s) stored.
            </p>
          </>
        ) : (
          <Empty message="No delivery record for this symbol." />
        )}
      </Panel>

      <Panel
        title="Settled sessions"
        subtitle={eod?.sessions ? `${eod.sessions} stored` : undefined}
      >
        {eod?.error ? (
          <Unavailable
            reason={eod.error}
            hint={["This reads the locally stored exchange archive.",
                   "Run the ingestion job to build the history."]}
          />
        ) : eod?.bars?.length ? (
          <>
            <div className="max-h-72 overflow-auto">
              <table className="w-full text-2xs">
                <thead className="sticky top-0 bg-surface">
                  <tr className="border-b border-line text-ink-muted">
                    <th className="py-1 text-left font-medium">Date</th>
                    <th className="py-1 text-right font-medium">Close</th>
                    <th className="py-1 text-right font-medium">Change</th>
                    <th className="py-1 text-right font-medium">Volume</th>
                    <th className="py-1 text-right font-medium">Turnover</th>
                  </tr>
                </thead>
                <tbody>
                  {[...eod.bars].reverse().map((bar: any) => (
                    <tr key={bar.date} className="border-b border-line/50">
                      <td className="py-1 text-ink-dim">{bar.date}</td>
                      <td className="num py-1 text-right text-ink">{num(bar.close)}</td>
                      <td className={`num py-1 text-right ${signClass(bar.change_pct)}`}>
                        {bar.change_pct != null ? pct(bar.change_pct) : "—"}
                      </td>
                      <td className="num py-1 text-right text-ink-muted">
                        {bar.volume != null ? num(bar.volume) : "—"}
                      </td>
                      <td className="num py-1 text-right text-ink-muted">
                        {bar.turnover != null ? compactInr(bar.turnover) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Notice tone="warn">{eod.note}</Notice>
          </>
        ) : (
          <Empty message="No stored sessions for this symbol yet." />
        )}
      </Panel>
    </div>
  );
}
