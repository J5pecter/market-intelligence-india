import Link from "next/link";
import { DataBadge, Empty, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { num, pct, signClass } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function IndicesPage() {
  const result = await apiFetch<any>("/api/indices", { auth: false });
  if (!result.data) {
    return <Panel title="Indices"><Unavailable reason={result.error} /></Panel>;
  }
  const data = result.data;

  return (
    <Panel
      title="Indices"
      actions={
        data.provenance ? (
          <DataBadge
            status={data.provenance.status}
            source={data.provenance.source}
            observedAt={data.provenance.observed_at}
          />
        ) : null
      }
      bodyClassName="p-0"
    >
      {!data.available ? (
        <div className="p-4"><Unavailable reason={data.reason} /></div>
      ) : data.rows.length === 0 ? (
        <div className="p-4"><Empty message="No indices returned." /></div>
      ) : (
        <div className="scroll-x">
          <table className="w-full min-w-[860px]">
            <thead className="border-b border-line">
              <tr>
                <th className="th">Index</th>
                <th className="th text-right">Last</th>
                <th className="th text-right">Change</th>
                <th className="th text-right">%</th>
                <th className="th text-right">Open</th>
                <th className="th text-right">High</th>
                <th className="th text-right">Low</th>
                <th className="th text-right">52w high</th>
                <th className="th text-right">52w low</th>
                <th className="th">Regime</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {data.rows.map((row: any) => (
                <tr key={row.symbol} className="hover:bg-raised/40">
                  <td className="td">
                    <Link
                      href={`/indices/${encodeURIComponent(row.symbol)}`}
                      className="font-semibold text-ink hover:text-accent"
                    >
                      {row.symbol}
                    </Link>
                    {row.is_demo && <span className="ml-1 text-[9px] text-warn">DEMO</span>}
                  </td>
                  <td className="td num text-right">{num(row.ltp)}</td>
                  <td className={`td num text-right ${signClass(row.change)}`}>{num(row.change)}</td>
                  <td className={`td num text-right ${signClass(row.change_pct)}`}>{pct(row.change_pct)}</td>
                  <td className="td num text-right text-ink-dim">{num(row.open)}</td>
                  <td className="td num text-right text-ink-dim">{num(row.high)}</td>
                  <td className="td num text-right text-ink-dim">{num(row.low)}</td>
                  <td className="td num text-right text-ink-muted">{num(row.week52_high)}</td>
                  <td className="td num text-right text-ink-muted">{num(row.week52_low)}</td>
                  <td className="td">
                    {row.regime ? <Tag tone="neutral">{row.regime}</Tag> : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
