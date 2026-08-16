import Link from "next/link";
import { ResearchCard, ResearchCallCard } from "@/components/ResearchCard";
import { Empty, Notice, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { num, pct } from "@/lib/format";

export const dynamic = "force-dynamic";

const SEGMENTS = [
  { key: "", label: "All" },
  { key: "EQUITY", label: "Stocks" },
  { key: "OPTION", label: "F&O" },
];

export default async function ResearchPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams({ limit: "60" });
  if (params.segment) query.set("segment", params.segment);
  if (params.status) query.set("status", params.status);
  if (params.source_type) query.set("source_type", params.source_type);

  const [callsResult, performanceResult] = await Promise.all([
    apiFetch<{ calls: ResearchCallCard[]; legend: Record<string, string>; disclaimers: Record<string, string> }>(
      `/api/research/calls?${query}`,
      { auth: false },
    ),
    apiFetch<{ rows: any[]; note: string }>("/api/research/sources/performance", {
      auth: false,
    }),
  ]);

  if (!callsResult.data) {
    return (
      <Panel title="Research">
        <Unavailable reason={callsResult.error} />
      </Panel>
    );
  }

  const { calls, legend, disclaimers } = callsResult.data;

  return (
    <div className="space-y-3">
      <Panel
        title="Research calls"
        subtitle="Status is recomputed from live price on every read"
        actions={
          <div className="flex gap-1">
            {SEGMENTS.map((segment) => (
              <Link
                key={segment.key}
                href={segment.key ? `/research?segment=${segment.key}` : "/research"}
                className={
                  (params.segment || "") === segment.key
                    ? "chip border-accent/50 bg-accent/10 text-accent"
                    : "chip border-line bg-raised text-ink-muted"
                }
              >
                {segment.label}
              </Link>
            ))}
          </div>
        }
      >
        <div className="space-y-2">
          {Object.entries(legend).map(([key, description]) => (
            <p key={key} className="text-2xs text-ink-muted">
              <Tag tone={key === "EXTERNAL_RESEARCH" ? "info" : "accent"}>
                {key === "EXTERNAL_RESEARCH" ? "Third-party" : "Platform"}
              </Tag>{" "}
              {description}
            </p>
          ))}
        </div>
      </Panel>

      {calls.length === 0 ? (
        <Panel>
          <Empty message="No published research calls match this filter." />
        </Panel>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {calls.map((call) => (
            <ResearchCard key={call.id} call={call} />
          ))}
        </div>
      )}

      {performanceResult.data?.rows?.length ? (
        <Panel
          title="Source performance"
          subtitle={performanceResult.data.note}
          bodyClassName="p-0"
        >
          <div className="scroll-x">
            <table className="w-full min-w-[820px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Source</th>
                  <th className="th text-right">Calls</th>
                  <th className="th text-right">Resolved</th>
                  <th className="th text-right">Targets hit</th>
                  <th className="th text-right">Stops hit</th>
                  <th className="th text-right">Hit rate</th>
                  <th className="th text-right">Avg return</th>
                  <th className="th text-right">Best</th>
                  <th className="th text-right">Worst</th>
                  <th className="th">Caveat</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {performanceResult.data.rows.map((row: any) => (
                  <tr key={row.source}>
                    <td className="td max-w-[220px] truncate text-ink">{row.source}</td>
                    <td className="td num text-right">{row.total_calls}</td>
                    <td className="td num text-right">{row.closed_calls}</td>
                    <td className="td num text-right text-pos">{row.targets_hit}</td>
                    <td className="td num text-right text-neg">{row.stops_hit}</td>
                    <td className="td num text-right">{row.hit_rate_pct !== null ? pct(row.hit_rate_pct, 1, false) : "—"}</td>
                    <td className="td num text-right">{pct(row.average_return_pct)}</td>
                    <td className="td num text-right text-pos">{pct(row.best_return_pct)}</td>
                    <td className="td num text-right text-neg">{pct(row.worst_return_pct)}</td>
                    <td className="td max-w-[260px] whitespace-normal text-2xs text-warn">
                      {row.sample_warning}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}

      <Panel title="Disclosures">
        <div className="space-y-2 text-2xs leading-relaxed text-ink-muted">
          <p>{disclaimers.primary}</p>
          <p>{disclaimers.external_research}</p>
          <p>{disclaimers.generated_signal}</p>
        </div>
      </Panel>
    </div>
  );
}
