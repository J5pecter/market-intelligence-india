import Link from "next/link";
import { Empty, Notice, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { DASH, dateIST, inr, num, pct, signClass } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function IpoPage() {
  const result = await apiFetch<any>("/api/ipo", { auth: false });
  if (!result.data) {
    return (
      <Panel title="IPO">
        <Unavailable reason={result.error} />
      </Panel>
    );
  }
  const { rows, gmp_disclaimer } = result.data;

  return (
    <div className="space-y-3">
      <Notice tone="warn">{gmp_disclaimer}</Notice>

      <Panel title="IPO tracker" subtitle={`${rows.length} issues recorded`} bodyClassName="p-0">
        {rows.length === 0 ? (
          <div className="p-4">
            <Empty message="No IPOs recorded. Add them through the admin panel or configure an IPO provider." />
          </div>
        ) : (
          <div className="scroll-x">
            <table className="w-full min-w-[1100px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Company</th>
                  <th className="th">Status</th>
                  <th className="th">Dates</th>
                  <th className="th text-right">Price band</th>
                  <th className="th text-right">Lot</th>
                  <th className="th text-right">Min amount</th>
                  <th className="th text-right">Issue size</th>
                  <th className="th text-right">GMP</th>
                  <th className="th text-right">Subscription</th>
                  <th className="th text-right">Research</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {rows.map((row: any) => (
                  <tr key={row.id} className="hover:bg-raised/40">
                    <td className="td">
                      <Link href={`/ipo/${row.id}`} className="font-medium text-ink hover:text-accent">
                        {row.company_name}
                      </Link>
                      {row.is_demo && <span className="ml-1 text-[9px] text-warn">DEMO</span>}
                      <div className="text-2xs text-ink-muted">{row.industry || row.type}</div>
                    </td>
                    <td className="td">
                      <Tag tone={row.status === "OPEN" ? "pos" : row.status === "LISTED" ? "info" : "neutral"}>
                        {row.status}
                      </Tag>
                    </td>
                    <td className="td text-2xs text-ink-dim">
                      {dateIST(row.open_date)} → {dateIST(row.close_date)}
                      {row.listing_date && (
                        <div className="text-ink-muted">Lists {dateIST(row.listing_date)}</div>
                      )}
                    </td>
                    <td className="td num text-right">
                      {inr(row.price_band[0], 0)}–{inr(row.price_band[1], 0)}
                    </td>
                    <td className="td num text-right">{row.lot_size ?? DASH}</td>
                    <td className="td num text-right text-ink-dim">
                      {inr(row.retail_min_investment, 0)}
                    </td>
                    <td className="td num text-right text-ink-dim">
                      {row.issue_size_cr ? `₹${num(row.issue_size_cr, 0)} Cr` : DASH}
                    </td>
                    <td className="td text-right">
                      <div className={`num ${signClass(row.gmp)}`}>
                        {row.gmp !== null ? `₹${num(row.gmp, 0)}` : DASH}
                      </div>
                      <div className="text-2xs text-ink-muted">
                        {row.gmp_pct !== null ? pct(row.gmp_pct, 1, false) : ""}
                      </div>
                    </td>
                    <td className="td num text-right">
                      {row.subscription_total !== null ? `${num(row.subscription_total, 2)}×` : DASH}
                    </td>
                    <td className="td text-right">
                      {row.research_score !== null ? (
                        <>
                          <div className="num font-semibold text-accent">
                            {num(row.research_score, 0)}
                          </div>
                          <div className="text-2xs text-ink-muted">{row.research_label}</div>
                        </>
                      ) : (
                        <span className="text-2xs text-ink-muted">Not scored</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
