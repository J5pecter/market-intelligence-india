"use client";

import { useEffect, useState } from "react";
import { AuthGate } from "@/components/AuthGate";
import {
  DataBadge, Empty, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { DASH, compactInr, dateTimeIST, inr, num, pct, signClass } from "@/lib/format";

export default function PortfolioPage() {
  return <AuthGate>{() => <PortfolioView />}</AuthGate>;
}

function PortfolioView() {
  const [tab, setTab] = useState<"holdings" | "paper">("holdings");
  const [portfolio, setPortfolio] = useState<any>(null);
  const [paper, setPaper] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const [portfolioResponse, paperResponse] = await Promise.all([
      api.get<any>("/api/portfolio"),
      api.get<any>("/api/paper"),
    ]);
    setPortfolio(portfolioResponse.data ?? { error: portfolioResponse.error });
    setPaper(paperResponse.data ?? { error: paperResponse.error });
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, []);

  if (loading) return <Panel><Loading /></Panel>;

  return (
    <div className="space-y-3">
      <div className="flex gap-1 border-b border-line">
        {(["holdings", "paper"] as const).map((key) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={
              tab === key
                ? "border-b-2 border-accent px-3 py-2 text-xs font-medium text-accent"
                : "border-b-2 border-transparent px-3 py-2 text-xs text-ink-muted hover:text-ink"
            }
          >
            {key === "holdings" ? "Holdings" : "Paper trading"}
          </button>
        ))}
      </div>

      {tab === "holdings" && portfolio && !portfolio.error && (
        <>
          <Panel title="Portfolio summary">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              <Stat label="Invested" value={compactInr(portfolio.totals.invested)} />
              <Stat label="Market value" value={compactInr(portfolio.totals.market_value)} />
              <Stat
                label="Unrealised P&L"
                value={compactInr(portfolio.totals.unrealised_pnl)}
                tone={portfolio.totals.unrealised_pnl >= 0 ? "pos" : "neg"}
              />
              <Stat label="Unrealised %" value={pct(portfolio.totals.unrealised_pnl_pct)} />
              <Stat label="Realised P&L" value={compactInr(portfolio.totals.realised_pnl)} />
              <Stat label="Dividends" value={compactInr(portfolio.totals.dividend_income)} />
              <Stat
                label="XIRR"
                value={portfolio.xirr?.value !== null ? pct(portfolio.xirr.value) : DASH}
                hint={portfolio.xirr?.note}
              />
              <Stat
                label="Pricing coverage"
                value={`${num(portfolio.totals.pricing_coverage_pct, 0)}%`}
                tone={portfolio.totals.pricing_coverage_pct < 100 ? "warn" : undefined}
              />
            </div>
            {portfolio.warnings?.length > 0 && (
              <div className="mt-2 space-y-1.5">
                {portfolio.warnings.map((warning: string) => (
                  <Notice key={warning} tone="warn">{warning}</Notice>
                ))}
              </div>
            )}
          </Panel>

          <AddHolding onSaved={load} />

          <Panel title="Holdings" bodyClassName="p-0">
            {portfolio.holdings?.length === 0 ? (
              <div className="p-4"><Empty message={portfolio.note || "No holdings entered."} /></div>
            ) : (
              <div className="scroll-x">
                <table className="w-full min-w-[900px]">
                  <thead className="border-b border-line">
                    <tr>
                      <th className="th">Symbol</th>
                      <th className="th">Sector</th>
                      <th className="th text-right">Qty</th>
                      <th className="th text-right">Avg cost</th>
                      <th className="th text-right">Invested</th>
                      <th className="th text-right">LTP</th>
                      <th className="th text-right">Value</th>
                      <th className="th text-right">P&L</th>
                      <th className="th text-right">P&L %</th>
                      <th className="th">Data</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/50">
                    {portfolio.holdings.map((holding: any) => (
                      <tr key={holding.symbol}>
                        <td className="td font-semibold text-ink">{holding.symbol}</td>
                        <td className="td max-w-[140px] truncate text-ink-muted">
                          {holding.sector || "—"}
                        </td>
                        <td className="td num text-right">{num(holding.quantity, 0)}</td>
                        <td className="td num text-right">{num(holding.average_cost)}</td>
                        <td className="td num text-right">{compactInr(holding.invested)}</td>
                        <td className="td num text-right">{num(holding.ltp)}</td>
                        <td className="td num text-right">{compactInr(holding.market_value)}</td>
                        <td className={`td num text-right ${signClass(holding.unrealised_pnl)}`}>
                          {compactInr(holding.unrealised_pnl)}
                        </td>
                        <td className={`td num text-right ${signClass(holding.unrealised_pnl_pct)}`}>
                          {pct(holding.unrealised_pnl_pct)}
                        </td>
                        <td className="td">
                          <DataBadge status={holding.provenance?.status} compact />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          {portfolio.concentration && Object.keys(portfolio.concentration).length > 0 && (
            <div className="grid gap-3 lg:grid-cols-2">
              <Panel title="Concentration" subtitle={portfolio.concentration.interpretation}>
                <div className="grid grid-cols-2 gap-3">
                  <Stat
                    label="Largest position"
                    value={`${portfolio.concentration.largest_position.symbol} · ${num(portfolio.concentration.largest_position.weight_pct, 1)}%`}
                  />
                  <Stat label="Top 3 weight" value={`${num(portfolio.concentration.top_3_weight_pct, 1)}%`} />
                  <Stat label="Top 5 weight" value={`${num(portfolio.concentration.top_5_weight_pct, 1)}%`} />
                  <Stat label="Effective positions" value={num(portfolio.concentration.effective_positions, 1)} />
                </div>
              </Panel>

              <Panel title="Allocation" subtitle={portfolio.allocation?.basis}>
                <ul className="space-y-1.5">
                  {(portfolio.allocation?.by_sector || []).map((row: any) => (
                    <li key={row.name} className="flex items-center gap-2">
                      <span className="w-32 shrink-0 truncate text-2xs text-ink-dim">{row.name}</span>
                      <div className="h-3 flex-1 overflow-hidden rounded-sm bg-raised">
                        <div className="h-full bg-accent/60" style={{ width: `${row.weight_pct}%` }} aria-hidden />
                      </div>
                      <span className="num w-12 shrink-0 text-right text-2xs text-ink-muted">
                        {num(row.weight_pct, 1)}%
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            </div>
          )}
        </>
      )}

      {tab === "paper" && paper && !paper.error && (
        <>
          <Notice tone="info">{paper.notice}</Notice>

          <Panel title="Paper trading summary">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              <Stat label="Open positions" value={paper.summary.open_count} />
              <Stat label="Closed" value={paper.summary.closed_count} />
              <Stat
                label="Unrealised"
                value={compactInr(paper.summary.unrealised_pnl)}
                tone={paper.summary.unrealised_pnl >= 0 ? "pos" : "neg"}
              />
              <Stat
                label="Realised"
                value={compactInr(paper.summary.realised_pnl)}
                tone={paper.summary.realised_pnl >= 0 ? "pos" : "neg"}
              />
              <Stat label="Win rate" value={pct(paper.summary.win_rate_pct, 1, false)} />
              <Stat label="Total exposure" value={compactInr(paper.summary.total_exposure)} />
            </div>
            <p className="mt-2 text-2xs text-ink-muted">{paper.summary.max_drawdown_note}</p>
          </Panel>

          <OpenPaperPosition onSaved={load} />

          <Panel title="Open positions" bodyClassName="p-0">
            {paper.open_positions?.length === 0 ? (
              <div className="p-4"><Empty message="No open paper positions." /></div>
            ) : (
              <div className="scroll-x">
                <table className="w-full min-w-[900px]">
                  <thead className="border-b border-line">
                    <tr>
                      <th className="th">Symbol</th>
                      <th className="th">Side</th>
                      <th className="th text-right">Qty</th>
                      <th className="th text-right">Entry</th>
                      <th className="th text-right">LTP</th>
                      <th className="th text-right">P&L</th>
                      <th className="th text-right">P&L %</th>
                      <th className="th text-right">Stop</th>
                      <th className="th text-right">Target</th>
                      <th className="th"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/50">
                    {paper.open_positions.map((position: any) => (
                      <ClosableRow key={position.id} position={position} onClosed={load} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      )}

      {portfolio?.error && <Panel><Unavailable reason={portfolio.error} /></Panel>}
    </div>
  );
}

function AddHolding({ onSaved }: { onSaved: () => void }) {
  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [cost, setCost] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const response = await api.post("/api/portfolio/holdings", {
      symbol: symbol.toUpperCase(),
      quantity: Number(quantity),
      average_cost: Number(cost),
    });
    if (response.error) setError(response.error);
    else {
      setSymbol(""); setQuantity(""); setCost(""); setError(null);
      onSaved();
    }
  };

  return (
    <Panel title="Add or update a holding" subtitle="Manual entry only — nothing here places an order">
      <div className="flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="text-2xs text-ink-muted">Symbol</span>
          <input value={symbol} onChange={(e) => setSymbol(e.target.value)} className="field mt-0.5 w-32" />
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">Quantity</span>
          <input type="number" value={quantity} onChange={(e) => setQuantity(e.target.value)} className="field mt-0.5 w-28" />
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">Average cost</span>
          <input type="number" step="0.01" value={cost} onChange={(e) => setCost(e.target.value)} className="field mt-0.5 w-32" />
        </label>
        <button className="btn btn-accent" onClick={save}>Save</button>
      </div>
      {error && <Notice tone="neg">{error}</Notice>}
    </Panel>
  );
}

function OpenPaperPosition({ onSaved }: { onSaved: () => void }) {
  const [form, setForm] = useState({
    symbol: "", quantity: "", entry_price: "", stop_loss: "", target: "",
  });
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const response = await api.post("/api/paper/positions", {
      symbol: form.symbol.toUpperCase(),
      quantity: Number(form.quantity),
      entry_price: Number(form.entry_price),
      stop_loss: form.stop_loss ? Number(form.stop_loss) : null,
      target: form.target ? Number(form.target) : null,
    });
    if (response.error) setError(response.error);
    else {
      setForm({ symbol: "", quantity: "", entry_price: "", stop_loss: "", target: "" });
      setError(null);
      onSaved();
    }
  };

  return (
    <Panel title="Open a paper position">
      <div className="flex flex-wrap items-end gap-2">
        {(["symbol", "quantity", "entry_price", "stop_loss", "target"] as const).map((field) => (
          <label key={field} className="block">
            <span className="text-2xs text-ink-muted">{field.replace("_", " ")}</span>
            <input
              value={form[field]}
              onChange={(e) => setForm({ ...form, [field]: e.target.value })}
              className="field mt-0.5 w-28"
            />
          </label>
        ))}
        <button className="btn btn-accent" onClick={save}>Open</button>
      </div>
      {error && <Notice tone="neg">{error}</Notice>}
    </Panel>
  );
}

function ClosableRow({ position, onClosed }: { position: any; onClosed: () => void }) {
  const [exitPrice, setExitPrice] = useState("");
  const [closing, setClosing] = useState(false);

  const close = async () => {
    setClosing(true);
    await api.post(`/api/paper/positions/${position.id}/close`, {
      exit_price: Number(exitPrice || position.ltp || position.entry_price),
    });
    setClosing(false);
    onClosed();
  };

  return (
    <tr>
      <td className="td font-semibold text-ink">{position.symbol}</td>
      <td className="td"><Tag tone={position.side === "LONG" ? "pos" : "neg"}>{position.side}</Tag></td>
      <td className="td num text-right">{position.quantity}</td>
      <td className="td num text-right">{num(position.entry_price)}</td>
      <td className="td num text-right">{num(position.ltp)}</td>
      <td className={`td num text-right ${signClass(position.unrealised_pnl)}`}>
        {num(position.unrealised_pnl, 0)}
      </td>
      <td className={`td num text-right ${signClass(position.unrealised_pnl_pct)}`}>
        {pct(position.unrealised_pnl_pct)}
      </td>
      <td className="td num text-right text-neg">{num(position.stop_loss)}</td>
      <td className="td num text-right text-pos">{num(position.target)}</td>
      <td className="td">
        <div className="flex items-center gap-1">
          <input
            value={exitPrice}
            onChange={(e) => setExitPrice(e.target.value)}
            placeholder={String(position.ltp ?? "")}
            className="field w-20 py-0.5"
            aria-label="Exit price"
          />
          <button className="btn px-2 py-0.5" onClick={close} disabled={closing}>
            Close
          </button>
        </div>
      </td>
    </tr>
  );
}
