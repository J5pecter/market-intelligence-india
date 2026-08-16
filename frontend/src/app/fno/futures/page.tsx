import { DataBadge, Empty, Notice, Panel, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { compactNum, dateIST, num, pct, signClass, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function FuturesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const symbol = (params.symbol || "NIFTY").toUpperCase();
  const result = await apiFetch<any>(`/api/fno/futures/${symbol}`, { auth: false });
  const data = result.data;

  return (
    <div className="space-y-3">
      <Panel
        title={`Futures — ${symbol}`}
        actions={
          <form action="/fno/futures" method="get" className="flex gap-1.5">
            <input
              name="symbol"
              defaultValue={symbol}
              className="field w-32"
              aria-label="Underlying symbol"
            />
            <button className="btn btn-accent" type="submit">Load</button>
          </form>
        }
        bodyClassName="p-0"
      >
        {!data?.available ? (
          <div className="p-4">
            <Unavailable
              reason={data?.reason || result.error}
              hint={[
                "The NSE adapter is off by default — enable ENABLE_NSE_PROVIDER after reviewing NSE's terms.",
                "Or configure a licensed feed / broker API, or enter contracts manually.",
              ]}
            />
          </div>
        ) : (
          <>
            <div className="scroll-x">
              <table className="w-full min-w-[980px]">
                <thead className="border-b border-line">
                  <tr>
                    <th className="th">Expiry</th>
                    <th className="th text-right">Days</th>
                    <th className="th text-right">Spot</th>
                    <th className="th text-right">Futures</th>
                    <th className="th text-right">Change</th>
                    <th className="th text-right">Basis</th>
                    <th className="th text-right">Basis %</th>
                    <th className="th text-right">Annualised</th>
                    <th className="th text-right">OI</th>
                    <th className="th text-right">OI chg</th>
                    <th className="th text-right">Contract value</th>
                    <th className="th">Build-up</th>
                    <th className="th">Data</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {data.contracts.map((row: any) => (
                    <tr key={row.expiry} className="hover:bg-raised/40">
                      <td className="td font-medium text-ink">{dateIST(row.expiry)}</td>
                      <td className="td num text-right text-ink-muted">{row.days_to_expiry}</td>
                      <td className="td num text-right">{num(row.spot)}</td>
                      <td className="td num text-right">{num(row.ltp)}</td>
                      <td className={`td num text-right ${signClass(row.change_pct)}`}>
                        {pct(row.change_pct)}
                      </td>
                      <td className={`td num text-right ${signClass(row.basis)}`}>
                        {num(row.basis)}
                      </td>
                      <td className={`td num text-right ${signClass(row.basis_pct)}`}>
                        {pct(row.basis_pct, 3)}
                      </td>
                      <td className="td num text-right text-ink-dim">
                        {pct(row.annualised_basis_pct, 2)}
                      </td>
                      <td className="td num text-right text-ink-dim">{compactNum(row.open_interest)}</td>
                      <td className={`td num text-right ${signClass(row.oi_change)}`}>
                        {compactNum(row.oi_change)}
                      </td>
                      <td className="td num text-right text-ink-dim">
                        {compactNum(row.contract_value)}
                      </td>
                      <td className="td text-2xs">{titleCase(row.buildup)}</td>
                      <td className="td">
                        <DataBadge status={row.data_status} source={row.source} compact />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="space-y-2 border-t border-line p-3.5">
              <p className="text-2xs leading-relaxed text-ink-muted">{data.basis_note}</p>
              <Notice tone="warn">{data.risk_disclosure}</Notice>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
