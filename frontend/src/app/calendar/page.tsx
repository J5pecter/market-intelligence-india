import Link from "next/link";
import { Empty, Notice, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { compactInr, dateIST, num, pct, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function CalendarPage() {
  const result = await apiFetch<any>("/api/calendar?days=60", { auth: false });
  if (!result.data) {
    return <Panel title="Market calendar"><Unavailable reason={result.error} /></Panel>;
  }
  const data = result.data;

  return (
    <div className="space-y-3">
      <Panel title="Exchange holidays" subtitle={`${data.from} → ${data.to}`}>
        <Notice tone="info">{data.holidays_note}</Notice>
        {data.holidays.length ? (
          <ul className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {data.holidays.map((holiday: any) => (
              <li key={holiday.date} className="flex items-center justify-between gap-2 rounded border border-line bg-raised/30 px-2.5 py-1.5 text-xs">
                <span className="text-ink">{dateIST(holiday.date)}</span>
                <span className="truncate text-2xs text-ink-muted">{holiday.description}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty message="No holidays in this window." />
        )}
      </Panel>

      <Panel title="Results calendar" bodyClassName="p-0">
        {data.earnings.length ? (
          <div className="scroll-x">
            <table className="w-full min-w-[560px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Date</th>
                  <th className="th">Symbol</th>
                  <th className="th">Quarter</th>
                  <th className="th">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {data.earnings.map((row: any, index: number) => (
                  <tr key={index}>
                    <td className="td">{dateIST(row.date)}</td>
                    <td className="td">
                      <Link href={`/stocks/${row.symbol}`} className="font-medium text-ink hover:text-accent">
                        {row.symbol}
                      </Link>
                      {row.is_demo && <span className="ml-1 text-[9px] text-warn">DEMO</span>}
                    </td>
                    <td className="td">{row.quarter}</td>
                    <td className="td text-2xs text-ink-muted">{row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4"><Empty message="No results scheduled in this window." /></div>
        )}
      </Panel>

      <Panel title="Corporate actions" bodyClassName="p-0">
        {data.corporate_actions.length ? (
          <div className="scroll-x">
            <table className="w-full min-w-[760px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Ex date</th>
                  <th className="th">Symbol</th>
                  <th className="th">Type</th>
                  <th className="th">Description</th>
                  <th className="th">Record</th>
                  <th className="th">Payment</th>
                  <th className="th text-right">Value</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {data.corporate_actions.map((row: any, index: number) => (
                  <tr key={index}>
                    <td className="td">{dateIST(row.ex_date)}</td>
                    <td className="td">
                      <Link href={`/stocks/${row.symbol}`} className="font-medium text-ink hover:text-accent">
                        {row.symbol}
                      </Link>
                    </td>
                    <td className="td"><Tag tone="neutral">{titleCase(row.type)}</Tag></td>
                    <td className="td max-w-[260px] truncate text-ink-dim">{row.description}</td>
                    <td className="td">{dateIST(row.record_date)}</td>
                    <td className="td">{dateIST(row.payment_date)}</td>
                    <td className="td num text-right">{num(row.value, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4"><Empty message="No corporate actions in this window." /></div>
        )}
      </Panel>

      <Panel title="Catalysts" bodyClassName="p-0">
        {data.catalysts.length ? (
          <div className="scroll-x">
            <table className="w-full min-w-[720px]">
              <thead className="border-b border-line">
                <tr>
                  <th className="th">Date</th>
                  <th className="th">Scope</th>
                  <th className="th">Symbol</th>
                  <th className="th">Event</th>
                  <th className="th">Category</th>
                  <th className="th">Expected impact</th>
                  <th className="th">Risk</th>
                  <th className="th">Confirmed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {data.catalysts.map((row: any, index: number) => (
                  <tr key={index}>
                    <td className="td">{dateIST(row.date)}</td>
                    <td className="td text-2xs text-ink-muted">{row.scope}</td>
                    <td className="td">{row.symbol || "Market"}</td>
                    <td className="td max-w-[280px] truncate text-ink-dim">{row.title}</td>
                    <td className="td text-2xs">{titleCase(row.category)}</td>
                    <td className="td">
                      <Tag tone={row.expected_impact === "HIGH" ? "neg" : row.expected_impact === "MEDIUM" ? "warn" : "neutral"}>
                        {row.expected_impact || "—"}
                      </Tag>
                    </td>
                    <td className="td text-2xs">{row.risk_level || "—"}</td>
                    <td className="td text-2xs text-ink-muted">{row.confirmed ? "Yes" : "No"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4"><Empty message="No catalysts recorded in this window." /></div>
        )}
      </Panel>
    </div>
  );
}
