"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { EvidencePanel } from "@/components/EvidencePanel";
import {
  DataBadge, Disclosure, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { DASH, compactNum, dateIST, dateTimeIST, num, pct, signClass } from "@/lib/format";

const BUILDUP_TONE: Record<string, string> = {
  LONG_BUILDUP: "text-pos",
  SHORT_COVERING: "text-pos",
  SHORT_BUILDUP: "text-neg",
  LONG_UNWINDING: "text-neg",
  UNCLEAR: "text-ink-muted",
};

export function OptionChainView({ initialSymbol }: { initialSymbol: string }) {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [input, setInput] = useState(initialSymbol);
  const [expiry, setExpiry] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [showGreeks, setShowGreeks] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const query = new URLSearchParams({ strikes: "15", greeks: String(showGreeks) });
    if (expiry) query.set("expiry", expiry);
    api.get(`/api/fno/options/${symbol}?${query}`, false).then((response) => {
      if (cancelled) return;
      setData(response.data ?? { available: false, reason: response.error });
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [symbol, expiry, showGreeks]);

  const maxOi = data?.rows
    ? Math.max(
        1,
        ...data.rows.flatMap((row: any) => [
          row.call?.open_interest || 0,
          row.put?.open_interest || 0,
        ]),
      )
    : 1;

  return (
    <div className="space-y-3">
      <Panel
        title="Option chain"
        actions={
          <form
            className="flex flex-wrap items-center gap-1.5"
            onSubmit={(event) => {
              event.preventDefault();
              setExpiry("");
              setSymbol(input.trim().toUpperCase());
            }}
          >
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              className="field w-32"
              placeholder="Symbol"
              aria-label="Underlying symbol"
            />
            {data?.available_expiries?.length > 0 && (
              <select
                value={expiry || data.expiry}
                onChange={(event) => setExpiry(event.target.value)}
                className="field w-36"
                aria-label="Expiry"
              >
                {data.available_expiries.map((value: string) => (
                  <option key={value} value={value}>
                    {dateIST(value)}
                  </option>
                ))}
              </select>
            )}
            <label className="flex items-center gap-1 text-2xs text-ink-dim">
              <input
                type="checkbox"
                checked={showGreeks}
                onChange={(event) => setShowGreeks(event.target.checked)}
              />
              Greeks
            </label>
            <button className="btn btn-accent" type="submit">Load</button>
          </form>
        }
      >
        {loading && <Loading label="Loading chain" />}

        {!loading && !data?.available && (
          <Unavailable reason={data?.reason} hint={data?.how_to_fix} />
        )}

        {!loading && data?.available && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <Stat label="Underlying" value={num(data.underlying_value)} />
              <Stat label="ATM strike" value={num(data.atm_strike, 0)} />
              <Stat
                label="PCR (OI)"
                value={num(data.totals?.pcr_oi, 3)}
                hint={data.totals?.pcr_formula}
              />
              <Stat label="PCR (volume)" value={num(data.totals?.pcr_volume, 3)} />
              <Stat label="Max pain" value={num(data.totals?.max_pain, 0)} />
              <Stat label="Call OI" value={compactNum(data.totals?.total_call_oi)} />
              <Stat label="Put OI" value={compactNum(data.totals?.total_put_oi)} />
              <div className="ml-auto">
                <DataBadge
                  status={data.provenance?.status}
                  source={data.provenance?.source}
                  observedAt={data.captured_at}
                />
              </div>
            </div>
            <Notice tone="warn">{data.risk_disclosure}</Notice>
          </div>
        )}
      </Panel>

      {!loading && data?.available && (
        <>
          <Panel title="Chain" bodyClassName="p-0">
            <div className="scroll-x">
              <table className="w-full min-w-[1180px] text-right">
                <thead className="border-b border-line">
                  <tr>
                    <th className="th text-right" colSpan={showGreeks ? 8 : 7}>
                      <span className="text-pos">CALLS</span>
                    </th>
                    <th className="th text-center">STRIKE</th>
                    <th className="th text-left" colSpan={showGreeks ? 8 : 7}>
                      <span className="text-neg">PUTS</span>
                    </th>
                  </tr>
                  <tr className="text-2xs">
                    <th className="th text-right">OI</th>
                    <th className="th text-right">Chg OI</th>
                    <th className="th text-right">Vol</th>
                    <th className="th text-right">IV</th>
                    {showGreeks && <th className="th text-right">Δ</th>}
                    <th className="th text-right">Bid</th>
                    <th className="th text-right">Ask</th>
                    <th className="th text-right">LTP</th>
                    <th className="th text-center">Strike</th>
                    <th className="th text-left">LTP</th>
                    <th className="th text-left">Bid</th>
                    <th className="th text-left">Ask</th>
                    {showGreeks && <th className="th text-left">Δ</th>}
                    <th className="th text-left">IV</th>
                    <th className="th text-left">Vol</th>
                    <th className="th text-left">Chg OI</th>
                    <th className="th text-left">OI</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/40">
                  {data.rows.map((row: any) => {
                    const isAtm = row.strike === data.atm_strike;
                    return (
                      <tr
                        key={row.strike}
                        className={clsx(
                          "hover:bg-raised/40",
                          isAtm && "bg-accent/5 font-semibold",
                        )}
                      >
                        <OiCell value={row.call?.open_interest} max={maxOi} side="call" />
                        <td className={`td num text-right ${signClass(row.call?.oi_change)}`}>
                          {compactNum(row.call?.oi_change)}
                        </td>
                        <td className="td num text-right text-ink-muted">
                          {compactNum(row.call?.volume)}
                        </td>
                        <td className="td num text-right text-ink-dim">
                          {num(row.call?.implied_volatility, 1)}
                        </td>
                        {showGreeks && (
                          <td className="td num text-right text-ink-muted">
                            {num(row.call?.greeks?.delta, 2)}
                          </td>
                        )}
                        <td className="td num text-right text-ink-muted">{num(row.call?.bid)}</td>
                        <td className="td num text-right text-ink-muted">{num(row.call?.ask)}</td>
                        <td className={`td num text-right ${signClass(row.call?.change)}`}>
                          {num(row.call?.ltp)}
                        </td>

                        <td
                          className={clsx(
                            "td num text-center",
                            isAtm ? "bg-accent/10 text-accent" : "text-ink",
                          )}
                          title={`${row.moneyness} · ${pct(row.distance_pct)} from spot`}
                        >
                          {num(row.strike, 0)}
                        </td>

                        <td className={`td num text-left ${signClass(row.put?.change)}`}>
                          {num(row.put?.ltp)}
                        </td>
                        <td className="td num text-left text-ink-muted">{num(row.put?.bid)}</td>
                        <td className="td num text-left text-ink-muted">{num(row.put?.ask)}</td>
                        {showGreeks && (
                          <td className="td num text-left text-ink-muted">
                            {num(row.put?.greeks?.delta, 2)}
                          </td>
                        )}
                        <td className="td num text-left text-ink-dim">
                          {num(row.put?.implied_volatility, 1)}
                        </td>
                        <td className="td num text-left text-ink-muted">
                          {compactNum(row.put?.volume)}
                        </td>
                        <td className={`td num text-left ${signClass(row.put?.oi_change)}`}>
                          {compactNum(row.put?.oi_change)}
                        </td>
                        <OiCell value={row.put?.open_interest} max={maxOi} side="put" />
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="border-t border-line px-3.5 py-2 text-2xs text-ink-muted">
              The ATM row is highlighted. Open-interest bars are scaled to the
              largest OI visible in this window, not to the whole chain.
            </p>
          </Panel>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Options intelligence">
              <EvidencePanel chain={data.evidence_chain} defaultOpen />
            </Panel>

            <div className="space-y-3">
              <Panel title="Key levels" subtitle={data.key_levels?.observation}>
                <div className="grid gap-3 sm:grid-cols-2">
                  <LevelList title="Highest call OI" rows={data.key_levels?.highest_call_oi} tone="neg" />
                  <LevelList title="Highest put OI" rows={data.key_levels?.highest_put_oi} tone="pos" />
                  <LevelList title="Largest call OI additions" rows={data.key_levels?.largest_call_oi_additions} tone="neg" />
                  <LevelList title="Largest put OI additions" rows={data.key_levels?.largest_put_oi_additions} tone="pos" />
                </div>
              </Panel>

              <Panel title="Implied volatility structure">
                {data.iv_structure?.available ? (
                  <div className="space-y-2">
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <Stat label="ATM IV" value={`${num(data.iv_structure.atm_iv, 1)}%`} />
                      <Stat label="Mean IV" value={`${num(data.iv_structure.mean_iv, 1)}%`} />
                      <Stat label="Min IV" value={`${num(data.iv_structure.min_iv, 1)}%`} />
                      <Stat label="Put-call skew" value={num(data.iv_structure.put_call_skew, 2)} />
                    </div>
                    {data.iv_structure.skew_reading && (
                      <p className="text-2xs leading-relaxed text-ink-dim">
                        {data.iv_structure.skew_reading}
                      </p>
                    )}
                  </div>
                ) : (
                  <Unavailable reason={data.iv_structure?.note} />
                )}
              </Panel>

              <Panel title="Greeks model assumptions">
                <dl className="space-y-1 text-2xs">
                  {Object.entries(data.greeks_assumptions || {}).map(([key, value]) => (
                    <div key={key} className="flex justify-between gap-3">
                      <dt className="text-ink-muted">{key.replace(/_/g, " ")}</dt>
                      <dd className="num text-right text-ink-dim">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </Panel>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function OiCell({
  value,
  max,
  side,
}: {
  value: number | null | undefined;
  max: number;
  side: "call" | "put";
}) {
  const width = value ? Math.min(100, (value / max) * 100) : 0;
  return (
    <td className={clsx("td num relative", side === "call" ? "text-right" : "text-left")}>
      <span
        className={clsx(
          "absolute inset-y-0.5 rounded-sm opacity-20",
          side === "call" ? "right-0 bg-neg" : "left-0 bg-pos",
        )}
        style={{ width: `${width}%` }}
        aria-hidden
      />
      <span className="relative">{compactNum(value)}</span>
    </td>
  );
}

function LevelList({
  title,
  rows,
  tone,
}: {
  title: string;
  rows: Array<{ strike: number; value: number }> | undefined;
  tone: "pos" | "neg";
}) {
  return (
    <div>
      <h4 className="mb-1 text-2xs uppercase tracking-wide text-ink-muted">{title}</h4>
      <ul className="space-y-0.5">
        {(rows || []).map((row) => (
          <li key={row.strike} className="flex justify-between text-2xs">
            <span className={tone === "pos" ? "text-pos" : "text-neg"}>
              {num(row.strike, 0)}
            </span>
            <span className="num text-ink-muted">{compactNum(row.value)}</span>
          </li>
        ))}
        {(!rows || rows.length === 0) && (
          <li className="text-2xs text-ink-muted">Not available.</li>
        )}
      </ul>
    </div>
  );
}
