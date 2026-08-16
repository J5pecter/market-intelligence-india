import Link from "next/link";
import { EvidencePanel } from "@/components/EvidencePanel";
import {
  DataBadge, Empty, Panel, ScoreBar, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { dateIST, num, pct, signClass } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function IndexDetailPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const decoded = decodeURIComponent(symbol);
  const result = await apiFetch<any>(
    `/api/indices/${encodeURIComponent(decoded)}`,
    { auth: false },
  );

  if (!result.data) {
    return <Panel title={decoded}><Unavailable reason={result.error} /></Panel>;
  }

  const data = result.data;
  const technical = data.technical;

  return (
    <div className="space-y-3">
      <Panel>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-bold tracking-wide text-ink">{data.symbol}</h1>
            <p className="mt-0.5 text-2xs text-ink-muted">
              Technical view computed from {technical?.bars_used ?? 0} bars
            </p>
          </div>
          <div className="text-right">
            <div className="num text-2xl font-bold text-ink">{num(technical?.last_close)}</div>
            <Tag tone="accent">{data.regime?.regime}</Tag>
          </div>
        </div>
        <div className="mt-3 border-t border-line pt-2.5">
          <DataBadge
            status={data.provenance?.status}
            source={data.provenance?.source}
            observedAt={data.provenance?.observed_at}
          />
        </div>
      </Panel>

      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="Market regime" className="lg:col-span-1">
          <div className="space-y-2">
            <Tag tone="accent">{data.regime?.regime}</Tag>
            <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-dim">
              {(data.regime?.reasons || []).map((reason: string) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            {data.regime?.stored_rationale && (
              <p className="text-2xs text-ink-muted">{data.regime.stored_rationale}</p>
            )}
            {data.india_vix !== null && data.india_vix !== undefined && (
              <Stat label="India VIX" value={num(data.india_vix, 2)} />
            )}
          </div>
        </Panel>

        <Panel title="Breadth" className="lg:col-span-1">
          {data.breadth?.available === false ? (
            <Unavailable reason={data.breadth.reason} />
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Advances" value={data.breadth?.advances} tone="pos" />
              <Stat label="Declines" value={data.breadth?.declines} tone="neg" />
              <Stat label="New highs" value={data.breadth?.new_highs} tone="pos" />
              <Stat label="New lows" value={data.breadth?.new_lows} tone="neg" />
              <Stat label="As of" value={dateIST(data.breadth?.as_of)} />
            </div>
          )}
        </Panel>

        <Panel title="Option positioning" className="lg:col-span-1">
          {data.options ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Expiry" value={dateIST(data.options.expiry)} />
                <Stat label="PCR (OI)" value={num(data.options.pcr_oi, 3)} />
                <Stat label="Max pain" value={num(data.options.max_pain, 0)} />
              </div>
              <p className="text-2xs leading-relaxed text-ink-dim">
                {data.options.explanation}
              </p>
              <Link href={`/fno/options?symbol=${data.symbol.replace(/\s+/g, "")}`} className="btn">
                Open the chain
              </Link>
            </div>
          ) : (
            <Empty message="No option chain is available for this index." />
          )}
        </Panel>
      </div>

      <Panel title="Technical evidence" subtitle={technical?.explanation}>
        <EvidencePanel chain={technical?.evidence_chain} defaultOpen />
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Top contributors" bodyClassName="p-0">
          {data.sector_contribution?.length ? (
            <div className="scroll-x">
              <table className="w-full min-w-[420px]">
                <thead className="border-b border-line">
                  <tr>
                    <th className="th">Symbol</th>
                    <th className="th text-right">Weight</th>
                    <th className="th text-right">Change</th>
                    <th className="th text-right">Contribution</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {data.sector_contribution.map((row: any) => (
                    <tr key={row.symbol}>
                      <td className="td">
                        <Link href={`/stocks/${row.symbol}`} className="text-ink hover:text-accent">
                          {row.symbol}
                        </Link>
                      </td>
                      <td className="td num text-right text-ink-muted">{num(row.weight_pct, 2)}%</td>
                      <td className={`td num text-right ${signClass(row.change_pct)}`}>
                        {pct(row.change_pct)}
                      </td>
                      <td className={`td num text-right ${signClass(row.contribution_pct)}`}>
                        {num(row.contribution_pct, 3)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4">
              <Empty message="No index constituents with weights are stored. Load them through the admin panel to see contribution analysis." />
            </div>
          )}
        </Panel>

        <Panel title="Support and resistance">
          {technical?.levels?.length ? (
            <ul className="space-y-1.5">
              {technical.levels.map((level: any) => (
                <li key={level.price} className="flex items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-2">
                    <Tag tone={level.kind === "SUPPORT" ? "pos" : "neg"}>{level.kind}</Tag>
                    <span className="num text-ink">{num(level.price)}</span>
                  </div>
                  <span className="text-2xs text-ink-muted">
                    {level.touches} touches · strength {level.strength}/100
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty message="No confirmed swing levels." />
          )}
        </Panel>
      </div>
    </div>
  );
}
