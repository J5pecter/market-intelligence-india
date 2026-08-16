import Link from "next/link";
import { DataBadge, Empty, Notice, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { compactNum, dateIST, dateTimeIST, num, pct, signClass, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function FnoPage() {
  const result = await apiFetch<any>("/api/fno/summary", { auth: false });

  if (!result.data) {
    return (
      <Panel title="F&O dashboard">
        <Unavailable reason={result.error} />
      </Panel>
    );
  }

  const data = result.data;

  return (
    <div className="space-y-3">
      <Panel title="Derivatives risk disclosure">
        <div className="space-y-2">
          <Notice tone="warn">{data.risk_disclosure.text}</Notice>
          {data.risk_disclosure.statistical_claims?.length > 0 ? (
            data.risk_disclosure.statistical_claims.map((claim: any) => (
              <div
                key={claim.id}
                className="rounded border border-neg/30 bg-neg/5 p-2.5 text-2xs leading-relaxed text-neg"
              >
                <p>{claim.claim}</p>
                <p className="mt-1 opacity-80">
                  Study period: <strong>{claim.study_period}</strong>. Source:{" "}
                  {claim.source_url ? (
                    <a
                      href={claim.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline underline-offset-2"
                    >
                      {claim.source_name}
                    </a>
                  ) : (
                    claim.source_name
                  )}
                  .{" "}
                  {claim.verified_on
                    ? `Verified on ${claim.verified_on}.`
                    : "Not yet verified against the current source by this deployment."}
                </p>
              </div>
            ))
          ) : (
            <p className="text-2xs text-ink-muted">
              No statistical claim is displayed: this deployment has not recorded
              a verified current source for one. {data.risk_disclosure.note}
            </p>
          )}
        </div>
      </Panel>

      {data.availability_note && <Unavailable reason={data.availability_note} />}

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel
          title="Option chains"
          actions={<Link href="/fno/options" className="btn px-2 py-1">Open chain</Link>}
          bodyClassName="p-0"
        >
          {data.option_chains?.length ? (
            <div className="scroll-x">
              <table className="w-full min-w-[720px]">
                <thead className="border-b border-line">
                  <tr>
                    <th className="th">Underlying</th>
                    <th className="th">Expiry</th>
                    <th className="th text-right">Spot</th>
                    <th className="th text-right">PCR (OI)</th>
                    <th className="th text-right">Max pain</th>
                    <th className="th text-right">Call OI</th>
                    <th className="th text-right">Put OI</th>
                    <th className="th">Data</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {data.option_chains.map((row: any) => (
                    <tr key={`${row.underlying}-${row.expiry}`} className="hover:bg-raised/40">
                      <td className="td">
                        <Link
                          href={`/fno/options?symbol=${row.underlying}`}
                          className="font-semibold text-ink hover:text-accent"
                        >
                          {row.underlying}
                        </Link>
                      </td>
                      <td className="td">{dateIST(row.expiry)}</td>
                      <td className="td num text-right">{num(row.underlying_value)}</td>
                      <td className="td num text-right">{num(row.pcr_oi, 3)}</td>
                      <td className="td num text-right">{num(row.max_pain, 0)}</td>
                      <td className="td num text-right text-ink-dim">{compactNum(row.total_call_oi)}</td>
                      <td className="td num text-right text-ink-dim">{compactNum(row.total_put_oi)}</td>
                      <td className="td">
                        <DataBadge status={row.status} source={row.source} observedAt={row.captured_at} compact />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4"><Empty message="No option-chain snapshots stored." /></div>
          )}
        </Panel>

        <Panel
          title="Futures"
          actions={<Link href="/fno/futures" className="btn px-2 py-1">All futures</Link>}
          bodyClassName="p-0"
        >
          {data.futures?.length ? (
            <div className="scroll-x">
              <table className="w-full min-w-[760px]">
                <thead className="border-b border-line">
                  <tr>
                    <th className="th">Underlying</th>
                    <th className="th">Expiry</th>
                    <th className="th text-right">Spot</th>
                    <th className="th text-right">Futures</th>
                    <th className="th text-right">Basis</th>
                    <th className="th text-right">OI</th>
                    <th className="th">Build-up</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {data.futures.map((row: any) => (
                    <tr key={`${row.underlying}-${row.expiry}`}>
                      <td className="td font-medium text-ink">{row.underlying}</td>
                      <td className="td">{dateIST(row.expiry)}</td>
                      <td className="td num text-right">{num(row.spot)}</td>
                      <td className="td num text-right">{num(row.ltp)}</td>
                      <td className={`td num text-right ${signClass(row.basis_pct)}`}>
                        {pct(row.basis_pct, 3)}
                      </td>
                      <td className="td num text-right text-ink-dim">{compactNum(row.open_interest)}</td>
                      <td className="td text-2xs">{titleCase(row.buildup)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-4"><Empty message="No futures snapshots stored." /></div>
          )}
        </Panel>
      </div>

      <Panel
        title="F&O eligible universe"
        subtitle={`${data.fno_universe?.length ?? 0} instruments marked F&O eligible in the instrument master`}
      >
        {data.fno_universe?.length ? (
          <div className="flex flex-wrap gap-1.5">
            {data.fno_universe.map((row: any) => (
              <Link
                key={row.symbol}
                href={`/fno/options?symbol=${row.symbol}`}
                className="chip border-line bg-raised text-ink-dim hover:border-accent/50 hover:text-accent"
                title={`${row.name} · lot ${row.lot_size ?? "?"}`}
              >
                {row.symbol}
              </Link>
            ))}
          </div>
        ) : (
          <Empty message="No F&O eligible instruments in the master. Run instrument_sync." />
        )}
      </Panel>
    </div>
  );
}
