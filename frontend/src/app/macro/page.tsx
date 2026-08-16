"use client";

/**
 * Macro context: RBI's policy corridor and World Bank series for India.
 *
 * Macro data is published with long lags — a CPI print lands weeks after the
 * month it measures, and World Bank annual series a year or more after the
 * year. Every panel here shows the reference period rather than the fetch
 * time, so an old figure is never mistaken for a current reading.
 */

import { useEffect, useState } from "react";
import {
  Empty, Loading, Notice, Panel, Stat, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { num } from "@/lib/format";

interface Rate {
  key: string;
  label: string;
  value_pct: number | null;
}

interface SeriesPoint {
  year: number;
  value: number;
  indicator_name: string | null;
}

const SERIES_LABELS: Record<string, string> = {
  gdp_growth: "GDP growth (annual %)",
  inflation_cpi: "CPI inflation (annual %)",
  current_account_pct_gdp: "Current account (% of GDP)",
  gross_savings_pct_gdp: "Gross savings (% of GDP)",
  fdi_net_inflows_usd: "FDI net inflows (USD)",
  unemployment_pct: "Unemployment (%)",
  market_cap_pct_gdp: "Market cap (% of GDP)",
  gdp_per_capita: "GDP per capita (USD)",
};

export default function MacroPage() {
  const [rates, setRates] = useState<any>(null);
  const [indicator, setIndicator] = useState("gdp_growth");
  const [series, setSeries] = useState<any>(null);
  const [funds, setFunds] = useState<any>(null);
  const [fundQuery, setFundQuery] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<any>("/api/macro/rates", false).then((r) => {
      setRates(r.data ?? { error: r.error });
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    setSeries(null);
    api.get<any>(`/api/macro/series/${indicator}`, false)
      .then((r) => setSeries(r.data ?? { error: r.error }));
  }, [indicator]);

  const searchFunds = async () => {
    if (fundQuery.trim().length < 2) return;
    setFunds(null);
    const r = await api.get<any>(
      `/api/macro/funds?q=${encodeURIComponent(fundQuery.trim())}&limit=40`, false);
    setFunds(r.data ?? { error: r.error });
  };

  const points: SeriesPoint[] = series?.series ?? [];
  const latest = points.length ? points[points.length - 1] : null;
  const max = points.length ? Math.max(...points.map((p) => Math.abs(p.value))) : 1;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Macro</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-dim">
          RBI&apos;s policy corridor, World Bank series for India and AMFI
          mutual fund NAVs. All published by the issuing body; none of it is
          modelled or estimated here.
        </p>
      </header>

      <Notice tone="warn">
        Macro data carries long publication lags. Each panel shows the reference
        period it describes — treat the latest available figure as the latest
        <em> published</em>, not as a reading for today.
      </Notice>

      {loading ? <Loading label="Loading macro data" /> : null}

      <Panel title="RBI policy corridor" subtitle="Reserve Bank of India">
        {rates?.error ? (
          <Unavailable
            reason={rates.error}
            hint={["RBI publishes no JSON API for this.",
                   "If their page layout changes, this reports unavailable rather than guessing a rate."]}
          />
        ) : rates?.rates?.length ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {rates.rates.map((r: Rate) => (
                <Stat
                  key={r.key}
                  label={r.label}
                  value={r.value_pct != null ? `${r.value_pct}%` : "—"}
                />
              ))}
            </div>
            <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
              {rates.provenance?.notes}
            </p>
          </>
        ) : (
          <Empty message="No policy rates available." />
        )}
      </Panel>

      <Panel
        title="India macro series"
        subtitle={latest ? `Latest published: ${latest.year}` : undefined}
        actions={
          <select
            value={indicator}
            onChange={(e) => setIndicator(e.target.value)}
            aria-label="Macro indicator"
            className="rounded border border-line-strong bg-raised px-2 py-1 text-2xs text-ink"
          >
            {Object.entries(SERIES_LABELS).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
        }
      >
        {series?.error ? (
          <Unavailable reason={series.error} />
        ) : points.length ? (
          <>
            <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Stat
                label={`Latest (${latest!.year})`}
                value={num(latest!.value, 2)}
                tone={latest!.value >= 0 ? "pos" : "neg"}
              />
              <Stat label="First year" value={String(points[0].year)} tone="muted" />
              <Stat label="Observations" value={String(points.length)} tone="muted" />
            </div>
            {/* Last 25 years, most recent last. A sparkline rather than a
                chart library: the shape is the point, not precision reading. */}
            <div className="flex h-24 items-end gap-0.5" role="img"
                 aria-label={`${SERIES_LABELS[indicator]} over time`}>
              {points.slice(-25).map((p) => (
                <div
                  key={p.year}
                  title={`${p.year}: ${p.value.toFixed(2)}`}
                  className={`flex-1 rounded-sm ${p.value >= 0 ? "bg-accent/60" : "bg-neg/60"}`}
                  style={{ height: `${Math.max(2, (Math.abs(p.value) / max) * 100)}%` }}
                />
              ))}
            </div>
            <div className="mt-1 flex justify-between text-2xs text-ink-muted">
              <span>{points.slice(-25)[0].year}</span>
              <span>{latest!.year}</span>
            </div>
            <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
              {series.provenance?.notes} · {series.provenance?.licence}
            </p>
          </>
        ) : (
          <Loading label="Loading series" />
        )}
      </Panel>

      <Panel title="Mutual fund NAVs" subtitle="AMFI India">
        <div className="flex gap-2">
          <input
            value={fundQuery}
            onChange={(e) => setFundQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") searchFunds(); }}
            placeholder="Search scheme name, e.g. Parag Parikh Flexi"
            aria-label="Search mutual fund schemes"
            className="flex-1 rounded border border-line-strong bg-raised px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-muted"
          />
          <button
            type="button"
            onClick={searchFunds}
            className="rounded border border-line-strong bg-raised px-3 py-1.5 text-xs text-ink hover:border-accent hover:text-accent"
          >
            Search
          </button>
        </div>

        {funds?.error ? (
          <div className="mt-3"><Unavailable reason={funds.error} /></div>
        ) : funds?.schemes?.length ? (
          <div className="mt-3 max-h-80 overflow-auto">
            <table className="w-full text-2xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-line text-ink-muted">
                  <th className="py-1 text-left font-medium">Scheme</th>
                  <th className="py-1 text-right font-medium">NAV</th>
                  <th className="py-1 text-right font-medium">As of</th>
                </tr>
              </thead>
              <tbody>
                {funds.schemes.map((s: any) => (
                  <tr key={s.scheme_code} className="border-b border-line/50">
                    <td className="py-1 pr-2 text-ink-dim">{s.scheme_name}</td>
                    <td className="num py-1 text-right text-ink">{num(s.nav, 4)}</td>
                    <td className="py-1 text-right text-ink-muted">{s.nav_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {funds.truncated ? (
              <p className="mt-2 text-2xs text-ink-muted">
                Showing the first {funds.schemes.length} of {funds.count} matches.
              </p>
            ) : null}
          </div>
        ) : funds ? (
          <div className="mt-3"><Empty message="No schemes matched that search." /></div>
        ) : null}

        <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
          NAVs are declared once per business day after the close. Mutual funds
          have no intraday value, so nothing here updates during the session.
        </p>
      </Panel>
    </div>
  );
}
