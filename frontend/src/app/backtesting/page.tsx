"use client";

import { useState } from "react";
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  Disclosure, Empty, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { DASH, inr, num, pct, titleCase } from "@/lib/format";

const CONDITION_FIELDS = [
  "close", "open", "high", "low", "volume",
  "sma_20", "sma_50", "sma_100", "sma_200",
  "ema_9", "ema_20", "ema_50",
  "rsi_14", "macd", "macd_signal", "macd_hist",
  "atr_14", "atr_pct", "adx_14", "plus_di", "minus_di",
  "bb_upper", "bb_lower", "bb_mid", "stoch_k", "stoch_d",
  "supertrend", "supertrend_dir", "vwap", "volume_ratio_20",
  "pct_from_52w_high", "pct_from_52w_low", "hist_vol_20",
];

const OPS = [">", "<", ">=", "<=", "cross_above", "cross_below"];

interface Condition {
  left: string;
  op: string;
  right: string;
}

export default function BacktestingPage() {
  const [symbols, setSymbols] = useState("HDFCBANK, INFY");
  const [name, setName] = useState("SMA 20/50 crossover");
  const [entry, setEntry] = useState<Condition[]>([
    { left: "sma_20", op: "cross_above", right: "sma_50" },
  ]);
  const [exit, setExit] = useState<Condition[]>([
    { left: "sma_20", op: "cross_below", right: "sma_50" },
  ]);
  const [stopLoss, setStopLoss] = useState(5);
  const [target, setTarget] = useState(12);
  const [slippage, setSlippage] = useState(0.05);
  const [capital, setCapital] = useState(100000);
  const [start, setStart] = useState("2024-01-01");
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [inSampleEnd, setInSampleEnd] = useState("2025-06-30");
  const [folds, setFolds] = useState(3);
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    setResult(null);
    const response = await api.post<any>("/api/backtests", {
      name,
      symbols: symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
      strategy: {
        name,
        direction: "LONG",
        entry_conditions: entry,
        entry_logic: "AND",
        exit_conditions: exit,
        exit_logic: "OR",
        stop_loss_pct: stopLoss || null,
        target_pct: target || null,
        slippage_pct: slippage,
        max_holding_bars: 60,
        segment: "EQUITY_DELIVERY",
      },
      start_date: start,
      end_date: end,
      in_sample_end: inSampleEnd || null,
      initial_capital: capital,
      walk_forward_folds: folds,
    }, false);
    setResult(response.data ?? { error: response.error });
    setLoading(false);
  };

  return (
    <div className="space-y-3">
      <Panel
        title="Strategy builder"
        actions={
          <button className="btn btn-accent" onClick={run} disabled={loading}>
            {loading ? "Running…" : "Run backtest"}
          </button>
        }
      >
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="space-y-2">
            <label className="block">
              <span className="text-2xs text-ink-muted">Strategy name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} className="field mt-0.5" />
            </label>
            <label className="block">
              <span className="text-2xs text-ink-muted">Universe (comma-separated symbols, max 25)</span>
              <input value={symbols} onChange={(e) => setSymbols(e.target.value)} className="field mt-0.5" />
            </label>
            <div className="grid grid-cols-3 gap-2">
              <label className="block">
                <span className="text-2xs text-ink-muted">Start</span>
                <input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="field mt-0.5" />
              </label>
              <label className="block">
                <span className="text-2xs text-ink-muted">End</span>
                <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="field mt-0.5" />
              </label>
              <label className="block">
                <span className="text-2xs text-ink-muted">In-sample ends</span>
                <input type="date" value={inSampleEnd} onChange={(e) => setInSampleEnd(e.target.value)} className="field mt-0.5" />
              </label>
            </div>
            <div className="grid grid-cols-4 gap-2">
              <label className="block">
                <span className="text-2xs text-ink-muted">Capital</span>
                <input type="number" value={capital} onChange={(e) => setCapital(Number(e.target.value))} className="field mt-0.5" />
              </label>
              <label className="block">
                <span className="text-2xs text-ink-muted">Stop %</span>
                <input type="number" step="0.5" value={stopLoss} onChange={(e) => setStopLoss(Number(e.target.value))} className="field mt-0.5" />
              </label>
              <label className="block">
                <span className="text-2xs text-ink-muted">Target %</span>
                <input type="number" step="0.5" value={target} onChange={(e) => setTarget(Number(e.target.value))} className="field mt-0.5" />
              </label>
              <label className="block">
                <span className="text-2xs text-ink-muted">Slippage %</span>
                <input type="number" step="0.01" value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} className="field mt-0.5" />
              </label>
            </div>
            <label className="block">
              <span className="text-2xs text-ink-muted">Walk-forward folds</span>
              <input type="number" min={0} max={10} value={folds} onChange={(e) => setFolds(Number(e.target.value))} className="field mt-0.5 w-24" />
            </label>
          </div>

          <div className="space-y-3">
            <ConditionEditor title="Entry conditions (all must hold)" conditions={entry} onChange={setEntry} />
            <ConditionEditor title="Exit conditions (any may fire)" conditions={exit} onChange={setExit} />
          </div>
        </div>
      </Panel>

      {loading && <Panel><Loading label="Running the backtest" /></Panel>}

      {result?.error && (
        <Panel><Unavailable reason={typeof result.error === "string" ? result.error : JSON.stringify(result.error)} /></Panel>
      )}

      {result && !result.error && (
        <>
          <Notice tone="info">{result.integrity_note}</Notice>

          {result.warnings?.length > 0 && (
            <Notice tone="warn">
              <ul className="list-inside list-disc space-y-0.5">
                {result.warnings.map((warning: string) => <li key={warning}>{warning}</li>)}
              </ul>
            </Notice>
          )}

          <Panel title="Results" subtitle={`${result.metrics.total_trades} trades over ${result.metrics.years} years`}>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              <Stat label="Net P&L" value={inr(result.metrics.net_pnl, 0)} tone={result.metrics.net_pnl >= 0 ? "pos" : "neg"} />
              <Stat label="Total return" value={pct(result.metrics.total_return_pct)} />
              <Stat label="CAGR" value={pct(result.metrics.cagr_pct)} />
              <Stat label="Win rate" value={pct(result.metrics.win_rate_pct, 1, false)} />
              <Stat label="Profit factor" value={num(result.metrics.profit_factor, 2)} />
              <Stat label="Max drawdown" value={pct(result.metrics.max_drawdown_pct)} tone="neg" />
              <Stat label="Sharpe" value={num(result.metrics.sharpe_ratio, 2)} hint="Computed on per-trade returns, annualised by trade frequency — not comparable with a daily-return Sharpe." />
              <Stat label="Sortino" value={num(result.metrics.sortino_ratio, 2)} />
              <Stat label="Expectancy / trade" value={inr(result.metrics.expectancy_per_trade, 0)} />
              <Stat label="Expectancy (R)" value={num(result.metrics.expectancy_r, 2)} />
              <Stat label="Avg win" value={inr(result.metrics.average_win, 0)} tone="pos" />
              <Stat label="Avg loss" value={inr(result.metrics.average_loss, 0)} tone="neg" />
              <Stat label="Max consecutive wins" value={result.metrics.max_consecutive_wins} />
              <Stat label="Max consecutive losses" value={result.metrics.max_consecutive_losses} />
              <Stat label="Total costs" value={inr(result.metrics.total_costs, 0)} tone="warn" />
              <Stat label="Avg holding (bars)" value={num(result.metrics.average_holding_bars, 1)} />
            </div>

            {result.equity_curve?.length > 1 && (
              <div className="mt-4 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={result.equity_curve}>
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#5d6f87" }} />
                    <YAxis tick={{ fontSize: 10, fill: "#5d6f87" }} width={60} domain={["auto", "auto"]} />
                    <Tooltip contentStyle={{ background: "#141b26", border: "1px solid #2b3849", fontSize: 11 }} />
                    <Area type="monotone" dataKey="equity" stroke="#2dd4bf" fill="#2dd4bf" fillOpacity={0.12} strokeWidth={1.5} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            <ul className="mt-3 list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
              {(result.metrics.notes || []).map((note: string) => <li key={note}>{note}</li>)}
            </ul>
          </Panel>

          {(result.in_sample_metrics || result.walk_forward) && (
            <div className="grid gap-3 lg:grid-cols-2">
              {result.in_sample_metrics && (
                <Panel title="In-sample vs out-of-sample" subtitle={result.degradation_note}>
                  <div className="scroll-x">
                    <table className="w-full min-w-[420px]">
                      <thead>
                        <tr>
                          <th className="th">Metric</th>
                          <th className="th text-right">In-sample</th>
                          <th className="th text-right">Out-of-sample</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line/50">
                        {["total_trades", "win_rate_pct", "profit_factor", "expectancy_per_trade", "max_drawdown_pct", "net_pnl"].map((key) => (
                          <tr key={key}>
                            <td className="td text-ink-dim">{titleCase(key)}</td>
                            <td className="td num text-right">{num(result.in_sample_metrics[key], 2)}</td>
                            <td className="td num text-right">{num(result.out_of_sample_metrics?.[key], 2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Panel>
              )}

              {result.walk_forward && (
                <Panel title="Walk-forward" subtitle={result.walk_forward.consistency_note || result.walk_forward.note}>
                  {result.walk_forward.folds?.length ? (
                    <div className="scroll-x">
                      <table className="w-full min-w-[520px]">
                        <thead>
                          <tr>
                            <th className="th">Fold</th>
                            <th className="th">Period</th>
                            <th className="th text-right">Trades</th>
                            <th className="th text-right">Win %</th>
                            <th className="th text-right">Net P&L</th>
                            <th className="th text-right">Max DD</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-line/50">
                          {result.walk_forward.folds.map((fold: any) => (
                            <tr key={fold.fold}>
                              <td className="td">{fold.fold}</td>
                              <td className="td text-2xs text-ink-muted">
                                {fold.period_start} → {fold.period_end}
                              </td>
                              <td className="td num text-right">{fold.trades}</td>
                              <td className="td num text-right">{num(fold.win_rate_pct, 1)}</td>
                              <td className={`td num text-right ${fold.net_pnl >= 0 ? "text-pos" : "text-neg"}`}>
                                {inr(fold.net_pnl, 0)}
                              </td>
                              <td className="td num text-right text-neg">{pct(fold.max_drawdown_pct)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <Empty message={result.walk_forward.note} />
                  )}
                </Panel>
              )}
            </div>
          )}

          <Panel title="Assumptions" subtitle="Change any of these and the results change">
            <dl className="space-y-1.5 text-2xs">
              {Object.entries(result.assumptions || {}).map(([key, value]) => (
                <div key={key} className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-6">
                  <dt className="shrink-0 text-ink-muted">{titleCase(key)}</dt>
                  <dd className="text-right leading-relaxed text-ink-dim">
                    {typeof value === "object" ? JSON.stringify(value) : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </Panel>

          <Panel title="Trades" bodyClassName="p-0">
            <div className="scroll-x max-h-[420px] overflow-y-auto">
              <table className="w-full min-w-[820px]">
                <thead className="sticky top-0 border-b border-line bg-surface">
                  <tr>
                    <th className="th">Symbol</th>
                    <th className="th">Entry</th>
                    <th className="th text-right">Price</th>
                    <th className="th">Exit</th>
                    <th className="th text-right">Price</th>
                    <th className="th">Reason</th>
                    <th className="th text-right">Net P&L</th>
                    <th className="th text-right">Return</th>
                    <th className="th text-right">Bars</th>
                    <th className="th">Sample</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {(result.trades || []).map((trade: any, index: number) => (
                    <tr key={index}>
                      <td className="td font-medium text-ink">{trade.symbol}</td>
                      <td className="td text-2xs">{trade.entry_date}</td>
                      <td className="td num text-right">{num(trade.entry_price)}</td>
                      <td className="td text-2xs">{trade.exit_date || DASH}</td>
                      <td className="td num text-right">{num(trade.exit_price)}</td>
                      <td className="td text-2xs text-ink-muted">{trade.exit_reason}</td>
                      <td className={`td num text-right ${trade.net_pnl >= 0 ? "text-pos" : "text-neg"}`}>
                        {num(trade.net_pnl, 0)}
                      </td>
                      <td className={`td num text-right ${trade.return_pct >= 0 ? "text-pos" : "text-neg"}`}>
                        {pct(trade.return_pct)}
                      </td>
                      <td className="td num text-right text-ink-muted">{trade.holding_bars}</td>
                      <td className="td"><Tag tone={trade.sample === "IS" ? "neutral" : "info"}>{trade.sample}</Tag></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

function ConditionEditor({
  title,
  conditions,
  onChange,
}: {
  title: string;
  conditions: Condition[];
  onChange: (next: Condition[]) => void;
}) {
  return (
    <div>
      <h3 className="mb-1.5 text-2xs uppercase tracking-wide text-ink-muted">{title}</h3>
      <div className="space-y-1.5">
        {conditions.map((condition, index) => (
          <div key={index} className="flex flex-wrap items-center gap-1.5">
            <select
              value={condition.left}
              onChange={(e) => {
                const next = [...conditions];
                next[index] = { ...condition, left: e.target.value };
                onChange(next);
              }}
              className="field w-40"
            >
              {CONDITION_FIELDS.map((field) => <option key={field} value={field}>{field}</option>)}
            </select>
            <select
              value={condition.op}
              onChange={(e) => {
                const next = [...conditions];
                next[index] = { ...condition, op: e.target.value };
                onChange(next);
              }}
              className="field w-32"
            >
              {OPS.map((op) => <option key={op} value={op}>{op}</option>)}
            </select>
            <input
              value={condition.right}
              onChange={(e) => {
                const next = [...conditions];
                next[index] = { ...condition, right: e.target.value };
                onChange(next);
              }}
              className="field w-40"
              placeholder="column or number"
              list="bt-fields"
            />
            <button
              className="btn px-2 py-1"
              onClick={() => onChange(conditions.filter((_, i) => i !== index))}
              aria-label="Remove condition"
            >
              ✕
            </button>
          </div>
        ))}
        <datalist id="bt-fields">
          {CONDITION_FIELDS.map((field) => <option key={field} value={field} />)}
        </datalist>
        <button
          className="btn"
          onClick={() => onChange([...conditions, { left: "rsi_14", op: ">", right: "55" }])}
        >
          Add condition
        </button>
      </div>
    </div>
  );
}
