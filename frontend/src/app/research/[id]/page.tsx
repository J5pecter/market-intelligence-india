import Link from "next/link";
import { ResearchCard } from "@/components/ResearchCard";
import { Notice, Panel, Stat, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { dateTimeIST, num, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ResearchCallPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const result = await apiFetch<any>(`/api/research/calls/${id}`, { auth: false });

  if (!result.data) {
    return (
      <Panel title="Research call">
        <Unavailable reason={result.error} />
      </Panel>
    );
  }

  const call = result.data;
  const evaluation = call.evaluation;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <ResearchCard call={call} />
        </div>

        <div className="space-y-3 lg:col-span-2">
          <Panel title="Status evaluation" subtitle={evaluation?.reason}>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Status" value={evaluation?.label} />
              <Stat label="Reference price" value={num(evaluation?.reference_price)} />
              <Stat label="Achieved" value={`${num(evaluation?.achieved_pct)}%`} />
              <Stat label="Risk / reward" value={evaluation?.risk_reward ? `1 : ${num(evaluation.risk_reward, 2)}` : "—"} />
              <Stat label="Risk per unit" value={num(evaluation?.risk_per_unit)} />
              <Stat label="Reward per unit" value={num(evaluation?.reward_per_unit)} />
              <Stat label="Potential from entry" value={`${num(evaluation?.potential_from_entry_pct)}%`} />
              <Stat label="Potential from LTP" value={`${num(evaluation?.potential_from_ltp_pct)}%`} />
            </div>

            {evaluation?.targets?.length > 0 && (
              <div className="scroll-x mt-3">
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
                    {evaluation.targets.map((target: any) => (
                      <tr key={target.index}>
                        <td className="td">T{target.index}</td>
                        <td className="td num text-right">{num(target.price)}</td>
                        <td className="td num text-right text-pos">{num(target.return_from_entry_pct)}%</td>
                        <td className="td num text-right">{num(target.return_from_ltp_pct)}%</td>
                        <td className="td num text-right">{target.r_multiple ? `${num(target.r_multiple, 2)}R` : "—"}</td>
                        <td className="td">{target.reached ? "Yes" : "No"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {evaluation?.warnings?.length > 0 && (
              <div className="mt-3 space-y-1.5">
                {evaluation.warnings.map((warning: string) => (
                  <Notice key={warning} tone="warn">{warning}</Notice>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="How every number was computed">
            <dl className="space-y-1.5 text-2xs">
              {Object.entries(evaluation?.formulas || {}).map(([key, formula]) => (
                <div key={key} className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-4">
                  <dt className="text-ink-muted">{titleCase(key)}</dt>
                  <dd className="num text-ink-dim">{String(formula)}</dd>
                </div>
              ))}
            </dl>
          </Panel>
        </div>
      </div>

      {(call.rationale || call.invalidation) && (
        <div className="grid gap-3 lg:grid-cols-2">
          {call.rationale && (
            <Panel title="Research rationale">
              <p className="text-xs leading-relaxed text-ink-dim">{call.rationale}</p>
            </Panel>
          )}
          {call.invalidation && (
            <Panel title="What would invalidate this">
              <p className="text-xs leading-relaxed text-ink-dim">{call.invalidation}</p>
            </Panel>
          )}
        </div>
      )}

      {(call.why_now?.length > 0 || call.why_not?.length > 0) && (
        <div className="grid gap-3 lg:grid-cols-2">
          <Panel title="Why now?">
            <ul className="list-inside list-disc space-y-1 text-xs text-ink-dim">
              {call.why_now.map((point: string, index: number) => (
                <li key={index}>{point}</li>
              ))}
              {call.why_now.length === 0 && <li className="text-ink-muted">Not recorded.</li>}
            </ul>
          </Panel>
          <Panel title="Why this may fail">
            <ul className="list-inside list-disc space-y-1 text-xs text-ink-dim">
              {call.why_not.map((point: string, index: number) => (
                <li key={index}>{point}</li>
              ))}
              {call.why_not.length === 0 && <li className="text-ink-muted">Not recorded.</li>}
            </ul>
          </Panel>
        </div>
      )}

      <Panel title="Version history" subtitle={call.version_note} bodyClassName="p-0">
        <div className="scroll-x">
          <table className="w-full min-w-[700px]">
            <thead className="border-b border-line">
              <tr>
                <th className="th">Version</th>
                <th className="th">Changed by</th>
                <th className="th">Reason</th>
                <th className="th">Fields changed</th>
                <th className="th">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {(call.versions || []).map((version: any) => (
                <tr key={version.version}>
                  <td className="td font-medium text-ink">v{version.version}</td>
                  <td className="td text-ink-dim">{version.changed_by || "system"}</td>
                  <td className="td max-w-[280px] whitespace-normal text-2xs text-ink-dim">
                    {version.change_reason}
                  </td>
                  <td className="td max-w-[240px] truncate text-2xs text-ink-muted">
                    {version.changed_fields
                      ? Object.keys(version.changed_fields).join(", ")
                      : "—"}
                  </td>
                  <td className="td text-2xs text-ink-muted">{dateTimeIST(version.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="flex gap-2">
        <Link href={`/stocks/${call.symbol}?tab=research`} className="btn btn-accent">
          Full evidence chain for {call.symbol}
        </Link>
        <Link href="/research" className="btn">All research</Link>
      </div>
    </div>
  );
}
