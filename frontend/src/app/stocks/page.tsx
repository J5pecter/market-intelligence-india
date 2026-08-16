import Link from "next/link";
import { DataBadge, Empty, Panel, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { compactInr, num, pct, signClass } from "@/lib/format";

export const dynamic = "force-dynamic";

interface StockRow {
  symbol: string;
  name: string;
  exchange: string;
  sector: string | null;
  industry: string | null;
  isin: string | null;
  is_fno_eligible: boolean;
  ltp: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
  week52_high: number | null;
  week52_low: number | null;
  market_cap: number | null;
  pe: number | null;
  data_status: string;
  observed_at: string | null;
  is_demo: boolean;
}

interface StocksPayload {
  rows: StockRow[];
  total: number;
  page: number;
  pages: number;
  note: string | null;
}

export default async function StocksPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams({
    page: params.page || "1",
    page_size: "60",
    sort: params.sort || "symbol",
    order: params.order || "asc",
  });
  if (params.q) query.set("q", params.q);
  if (params.sector) query.set("sector", params.sector);
  if (params.fno === "1") query.set("fno_only", "true");

  const result = await apiFetch<StocksPayload>(`/api/stocks?${query}`, {
    auth: false,
  });

  if (!result.data) {
    return (
      <Panel title="Stock universe">
        <Unavailable reason={result.error} />
      </Panel>
    );
  }

  const { rows, total, page, pages, note } = result.data;

  return (
    <Panel
      title="Stock universe"
      subtitle={`${total.toLocaleString("en-IN")} instruments in the master · page ${page} of ${Math.max(pages, 1)}`}
      bodyClassName="p-0"
      actions={
        <form className="flex items-center gap-1.5" action="/stocks" method="get">
          <input
            name="q"
            defaultValue={params.q || ""}
            placeholder="Filter symbol or name"
            className="field w-44"
            aria-label="Filter stocks"
          />
          <label className="flex items-center gap-1 text-2xs text-ink-dim">
            <input type="checkbox" name="fno" value="1" defaultChecked={params.fno === "1"} />
            F&amp;O only
          </label>
          <button className="btn" type="submit">Apply</button>
        </form>
      }
    >
      {rows.length === 0 ? (
        <div className="p-4">
          <Empty message="No instruments match. Run the instrument_sync job from the admin panel to import the universe." />
        </div>
      ) : (
        <div className="scroll-x">
          <table className="w-full min-w-[900px]">
            <thead className="sticky top-0 border-b border-line bg-surface">
              <tr>
                <th className="th">Symbol</th>
                <th className="th">Company</th>
                <th className="th">Sector</th>
                <th className="th text-right">LTP</th>
                <th className="th text-right">Change</th>
                <th className="th text-right">Volume</th>
                <th className="th text-right">52w range</th>
                <th className="th text-right">Mkt cap</th>
                <th className="th text-right">P/E</th>
                <th className="th">Data</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {rows.map((row) => (
                <tr key={`${row.symbol}-${row.exchange}`} className="hover:bg-raised/40">
                  <td className="td">
                    <Link href={`/stocks/${row.symbol}`} className="font-semibold text-ink hover:text-accent">
                      {row.symbol}
                    </Link>
                    {row.is_fno_eligible && (
                      <span className="ml-1 text-[9px] text-accent">F&amp;O</span>
                    )}
                  </td>
                  <td className="td max-w-[220px] truncate text-ink-dim">{row.name}</td>
                  <td className="td max-w-[150px] truncate text-ink-muted">
                    {row.sector || "—"}
                  </td>
                  <td className="td num text-right">{num(row.ltp)}</td>
                  <td className={`td num text-right ${signClass(row.change_pct)}`}>
                    {pct(row.change_pct)}
                  </td>
                  <td className="td num text-right text-ink-dim">
                    {row.volume ? row.volume.toLocaleString("en-IN") : "—"}
                  </td>
                  <td className="td num text-right text-ink-muted">
                    {num(row.week52_low, 0)} – {num(row.week52_high, 0)}
                  </td>
                  <td className="td num text-right text-ink-dim">
                    {compactInr(row.market_cap)}
                  </td>
                  <td className="td num text-right text-ink-dim">{num(row.pe, 1)}</td>
                  <td className="td">
                    <DataBadge
                      status={row.data_status}
                      observedAt={row.observed_at}
                      compact
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {note && <p className="border-t border-line px-3.5 py-2 text-2xs text-ink-muted">{note}</p>}

      {pages > 1 && (
        <nav className="flex items-center justify-between border-t border-line px-3.5 py-2.5 text-xs">
          <Link
            href={`/stocks?${new URLSearchParams({ ...params, page: String(Math.max(1, page - 1)) } as Record<string, string>)}`}
            className="btn"
            aria-disabled={page <= 1}
          >
            Previous
          </Link>
          <span className="text-ink-muted">
            Page {page} of {pages}
          </span>
          <Link
            href={`/stocks?${new URLSearchParams({ ...params, page: String(Math.min(pages, page + 1)) } as Record<string, string>)}`}
            className="btn"
            aria-disabled={page >= pages}
          >
            Next
          </Link>
        </nav>
      )}
    </Panel>
  );
}
