"use client";

import { useState } from "react";
import {
  Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  DataBadge, Disclosure, Empty, Notice, Panel, ScoreBar, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import {
  DASH, compactInr, dateIST, dateTimeIST, inr, num, pct, titleCase,
} from "@/lib/format";

export function IpoDetail({
  id,
  detail,
  research,
  gmp,
}: {
  id: string;
  detail: any;
  research: any;
  gmp: any;
}) {
  const ipo = detail.ipo;
  const assessment = research?.assessment;

  return (
    <div className="space-y-3">
      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-lg font-bold text-ink">{ipo.company_name}</h1>
              <Tag tone={ipo.status === "OPEN" ? "pos" : "neutral"}>{ipo.status}</Tag>
              <Tag tone="neutral">{ipo.type}</Tag>
              {ipo.is_demo && <Tag tone="warn">DEMO</Tag>}
            </div>
            <p className="mt-1 text-2xs text-ink-muted">
              {[ipo.industry, ipo.registrar, ipo.listing_exchanges]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          {assessment && (
            <div className="text-right">
              <div className="num text-2xl font-bold text-accent">
                {num(assessment.overall_score, 1)}
                <span className="text-xs text-ink-muted">/100</span>
              </div>
              <Tag tone="accent">{assessment.label}</Tag>
              <p className="mt-1 max-w-xs text-2xs text-ink-muted">
                Data completeness {num(assessment.data_completeness_pct, 0)}%
              </p>
            </div>
          )}
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3 border-t border-line pt-3 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="Price band" value={`${inr(ipo.price_band_low, 0)}–${inr(ipo.price_band_high, 0)}`} />
          <Stat label="Face value" value={inr(ipo.face_value, 0)} />
          <Stat label="Lot size" value={ipo.lot_size ?? DASH} />
          <Stat label="Retail minimum" value={inr(ipo.retail_min_investment, 0)} />
          <Stat label="Issue size" value={ipo.issue_size_cr ? `₹${num(ipo.issue_size_cr, 0)} Cr` : DASH} />
          <Stat label="Fresh / OFS" value={`${num(ipo.fresh_issue_cr, 0)} / ${num(ipo.ofs_cr, 0)} Cr`} />
          <Stat label="Opens" value={dateIST(ipo.open_date)} />
          <Stat label="Closes" value={dateIST(ipo.close_date)} />
          <Stat label="Allotment" value={dateIST(ipo.allotment_date)} />
          <Stat label="Lists" value={dateIST(ipo.listing_date)} />
          <Stat label="Listing price" value={inr(ipo.listing_price)} />
          <Stat label="Listing gain" value={pct(ipo.listing_gain_pct)} />
        </div>

        <div className="mt-3 flex items-center gap-2 border-t border-line pt-2.5">
          <DataBadge status={ipo.data_status} source={ipo.source} />
          {ipo.source_url && (
            <a
              href={ipo.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-2xs text-accent underline underline-offset-2"
            >
              Source document
            </a>
          )}
        </div>
      </Panel>

      {/* GMP */}
      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="Grey market premium" className="lg:col-span-2">
          <Notice tone="warn">{detail.gmp.disclaimer}</Notice>
          {detail.gmp.available ? (
            <>
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Current GMP" value={inr(detail.gmp.current, 0)} />
                <Stat label="As % of band" value={pct(detail.gmp.current_pct, 1, false)} />
                <Stat label="Estimated listing" value={inr(detail.gmp.estimated_listing_price, 0)} />
                <Stat label="Observed" value={dateTimeIST(detail.gmp.observed_on)} />
              </div>
              {gmp?.series?.length > 1 && (
                <div className="mt-4 h-52">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={gmp.series.map((point: any) => ({
                      date: dateIST(point.observed_on),
                      gmp: point.gmp,
                      pct: point.gmp_pct,
                    }))}>
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#5d6f87" }} />
                      <YAxis tick={{ fontSize: 10, fill: "#5d6f87" }} width={40} />
                      <Tooltip
                        contentStyle={{
                          background: "#141b26",
                          border: "1px solid #2b3849",
                          fontSize: 11,
                        }}
                      />
                      <Line
                        type="monotone"
                        dataKey="gmp"
                        stroke="#2dd4bf"
                        strokeWidth={1.5}
                        dot={false}
                        name="GMP (₹)"
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
              {gmp?.reading_note && (
                <p className="mt-2 text-2xs leading-relaxed text-ink-muted">
                  {gmp.reading_note}
                </p>
              )}
              <p className="mt-1 text-2xs text-ink-muted">
                Source: {detail.gmp.source}. {detail.gmp.confidence_note}
              </p>
            </>
          ) : (
            <Unavailable reason="No grey-market quote has been recorded for this issue." />
          )}
        </Panel>

        <Panel title="Subscription">
          {detail.subscription.available ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="QIB" value={`${num(detail.subscription.qib, 2)}×`} />
                <Stat label="NII" value={`${num(detail.subscription.nii, 2)}×`} />
                <Stat label="Retail" value={`${num(detail.subscription.retail, 2)}×`} />
                <Stat label="Total" value={`${num(detail.subscription.total, 2)}×`} />
              </div>
              <p className="text-2xs text-ink-muted">
                Day {detail.subscription.day} · {dateTimeIST(detail.subscription.observed_at)} ·{" "}
                {detail.subscription.source}
              </p>
            </div>
          ) : (
            <Unavailable reason="The issue has not opened, or no subscription data was recorded." />
          )}
        </Panel>
      </div>

      {/* Research assessment */}
      {assessment ? (
        <Panel title="Research assessment" subtitle={assessment.label_reason}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {assessment.components.map((component: any) => (
              <div key={component.key} className="rounded border border-line bg-raised/30 p-2.5">
                <ScoreBar
                  label={component.label}
                  score={component.score}
                  inverted={component.key === "risk"}
                  note={`Coverage ${component.coverage_pct}%`}
                />
                <ul className="mt-2 list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
                  {component.reasons.map((reason: string, index: number) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <Notice tone="info">{assessment.disclaimer}</Notice>
        </Panel>
      ) : (
        <Panel title="Research assessment">
          <Unavailable reason="The research assessment could not be computed." />
        </Panel>
      )}

      {/* Valuation + SWOT */}
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Valuation" subtitle={assessment?.valuation?.method}>
          {assessment?.valuation ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Implied P/E" value={num(assessment.valuation.implied_pe, 2)} />
                <Stat label="Peer median P/E" value={num(assessment.valuation.peer_median_pe, 2)} />
                <Stat
                  label="Premium to peers"
                  value={pct(assessment.valuation.premium_to_peer_pct, 1)}
                  tone={(assessment.valuation.premium_to_peer_pct ?? 0) > 0 ? "neg" : "pos"}
                />
                <Stat label="Verdict" value={assessment.valuation.verdict || DASH} />
              </div>
              {assessment.valuation.pe_note && (
                <p className="text-2xs text-warn">{assessment.valuation.pe_note}</p>
              )}
              {assessment.valuation.peer_note && (
                <p className="text-2xs text-ink-muted">{assessment.valuation.peer_note}</p>
              )}
            </div>
          ) : (
            <Empty message="No valuation could be computed." />
          )}
        </Panel>

        <Panel title="SWOT" subtitle="Every point carries the evidence that produced it">
          {assessment?.swot ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {(["strengths", "weaknesses", "opportunities", "threats"] as const).map((key) => (
                <div key={key}>
                  <h4
                    className={`mb-1 text-2xs font-semibold uppercase tracking-wide ${
                      key === "strengths" || key === "opportunities" ? "text-pos" : "text-neg"
                    }`}
                  >
                    {titleCase(key)}
                  </h4>
                  <ul className="space-y-1.5">
                    {(assessment.swot[key] || []).map((item: any, index: number) => (
                      <li key={index} className="text-2xs">
                        <p className="text-ink-dim">{item.point}</p>
                        <p className="mt-0.5 text-ink-muted">{item.evidence}</p>
                      </li>
                    ))}
                    {(assessment.swot[key] || []).length === 0 && (
                      <li className="text-2xs text-ink-muted">None recorded.</li>
                    )}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <Empty message="No SWOT could be built." />
          )}
        </Panel>
      </div>

      {/* Financials */}
      <Panel title="Financials from the offer document" bodyClassName="p-0">
        {detail.financials?.length ? (
          <div className="scroll-x">
            <table className="w-full min-w-[900px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Period</th>
                  <th className="th text-right">Revenue</th>
                  <th className="th text-right">EBITDA</th>
                  <th className="th text-right">Margin</th>
                  <th className="th text-right">PAT</th>
                  <th className="th text-right">Net margin</th>
                  <th className="th text-right">EPS</th>
                  <th className="th text-right">Net worth</th>
                  <th className="th text-right">Debt</th>
                  <th className="th text-right">ROE</th>
                  <th className="th text-right">ROCE</th>
                  <th className="th">Source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {detail.financials.map((row: any) => (
                  <tr key={row.period_label}>
                    <td className="td font-medium text-ink">{row.period_label}</td>
                    <td className="td num text-right">{num(row.revenue, 0)}</td>
                    <td className="td num text-right">{num(row.ebitda, 0)}</td>
                    <td className="td num text-right">{pct(row.ebitda_margin, 1, false)}</td>
                    <td className="td num text-right">{num(row.pat, 0)}</td>
                    <td className="td num text-right">{pct(row.net_margin, 1, false)}</td>
                    <td className="td num text-right">{num(row.eps, 2)}</td>
                    <td className="td num text-right">{num(row.net_worth, 0)}</td>
                    <td className="td num text-right">{num(row.total_debt, 0)}</td>
                    <td className="td num text-right">{pct(row.roe, 1, false)}</td>
                    <td className="td num text-right">{pct(row.roce, 1, false)}</td>
                    <td className="td text-2xs text-ink-muted">{row.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4">
            <Empty message="No financial history extracted for this issue." />
          </div>
        )}
      </Panel>

      {/* Risks */}
      <Panel title="Risk factors" subtitle="As recorded from the offer document">
        {detail.risk_factors?.length ? (
          <ul className="space-y-2">
            {detail.risk_factors.map((risk: any, index: number) => (
              <li key={index} className="rounded border border-line bg-raised/30 p-2.5">
                <div className="flex items-center gap-2">
                  <Tag tone={risk.severity === "HIGH" ? "neg" : risk.severity === "MEDIUM" ? "warn" : "neutral"}>
                    {risk.severity}
                  </Tag>
                  <span className="text-2xs uppercase tracking-wide text-ink-muted">
                    {titleCase(risk.category)}
                  </span>
                  {risk.quantum && (
                    <span className="num text-2xs text-ink-dim">
                      {num(risk.quantum, 1)}
                      {risk.quantum_unit}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs leading-relaxed text-ink-dim">{risk.description}</p>
                <p className="mt-0.5 text-2xs text-ink-muted">Source: {risk.source}</p>
              </li>
            ))}
          </ul>
        ) : (
          <Notice tone="warn">
            No risk factors are recorded. That is a gap in this deployment&rsquo;s
            data, not evidence that the offer document lists none.
          </Notice>
        )}
      </Panel>

      <ApplicationSimulator ipoId={id} lotSize={ipo.lot_size} price={ipo.price_band_high} gmpValue={detail.gmp.current} />

      {/* Documents */}
      {detail.documents?.length > 0 && (
        <Panel title="Offer documents">
          <ul className="space-y-1">
            {detail.documents.map((document: any, index: number) => (
              <li key={index} className="flex items-center justify-between gap-2 text-xs">
                <a
                  href={document.url || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-accent underline underline-offset-2"
                >
                  {document.title || document.type}
                </a>
                <span className="text-2xs text-ink-muted">
                  {dateIST(document.date)} · {document.extraction_status}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {assessment?.limitations?.length > 0 && (
        <Panel title="Limitations of this analysis">
          <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
            {assessment.limitations.map((limit: string) => (
              <li key={limit}>{limit}</li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function ApplicationSimulator({
  ipoId,
  lotSize,
  price,
  gmpValue,
}: {
  ipoId: string;
  lotSize: number | null;
  price: number | null;
  gmpValue: number | null;
}) {
  const [lots, setLots] = useState(1);
  const [capital, setCapital] = useState(200000);
  const [gmp, setGmp] = useState(gmpValue ?? 0);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setError(null);
    const response = await api.post<any>(`/api/ipo/${ipoId}/simulate`, {
      lots, capital, gmp,
    }, false);
    if (response.error) setError(response.error);
    else setResult(response.data);
  };

  if (!lotSize || !price) {
    return (
      <Panel title="Application simulator">
        <Unavailable reason="This issue has no lot size or price band recorded." />
      </Panel>
    );
  }

  return (
    <Panel title="Application simulator" subtitle="Arithmetic only — no probability is attached to any scenario">
      <div className="grid gap-3 sm:grid-cols-4">
        <label className="block">
          <span className="text-2xs text-ink-muted">Capital (₹)</span>
          <input
            type="number"
            value={capital}
            onChange={(event) => setCapital(Number(event.target.value))}
            className="field mt-0.5"
          />
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">Lots ({lotSize} shares each)</span>
          <input
            type="number"
            min={1}
            value={lots}
            onChange={(event) => setLots(Number(event.target.value))}
            className="field mt-0.5"
          />
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">GMP (₹, unofficial)</span>
          <input
            type="number"
            value={gmp}
            onChange={(event) => setGmp(Number(event.target.value))}
            className="field mt-0.5"
          />
        </label>
        <div className="flex items-end">
          <button className="btn btn-accent w-full" onClick={run}>
            Calculate
          </button>
        </div>
      </div>

      {error && <Notice tone="neg">{error}</Notice>}

      {result && (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Shares applied for" value={result.shares_applied_for} />
            <Stat label="Application amount" value={inr(result.application_amount, 0)} />
            <Stat
              label="Affordable"
              value={result.affordable ? "Yes" : "No"}
              tone={result.affordable ? "pos" : "neg"}
            />
            <Stat label="Lot size" value={lotSize} />
          </div>

          <div className="scroll-x">
            <table className="w-full min-w-[620px]">
              <thead>
                <tr>
                  <th className="th">Scenario</th>
                  <th className="th text-right">Assumed listing</th>
                  <th className="th text-right">Gross gain</th>
                  <th className="th text-right">Gain %</th>
                  <th className="th">Description</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {result.scenarios.map((scenario: any) => (
                  <tr key={scenario.scenario}>
                    <td className="td font-medium text-ink">{scenario.scenario}</td>
                    <td className="td num text-right">{inr(scenario.assumed_listing_price, 0)}</td>
                    <td className={`td num text-right ${scenario.gross_gain >= 0 ? "text-pos" : "text-neg"}`}>
                      {inr(scenario.gross_gain, 0)}
                    </td>
                    <td className={`td num text-right ${scenario.gain_pct >= 0 ? "text-pos" : "text-neg"}`}>
                      {pct(scenario.gain_pct)}
                    </td>
                    <td className="td max-w-[320px] whitespace-normal text-2xs text-ink-muted">
                      {scenario.description}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Notice tone="warn">{result.allotment_note}</Notice>
          <Notice tone="warn">{result.disclaimer}</Notice>
        </div>
      )}
    </Panel>
  );
}
