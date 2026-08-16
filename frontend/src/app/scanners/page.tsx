"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Empty, Loading, Notice, Panel, Tag, Unavailable,
} from "@/components/primitives";
import { api } from "@/lib/api";
import { dateIST, num, pct, signClass, titleCase } from "@/lib/format";

interface ScannerDef {
  key: string;
  name: string;
  category: string;
  description: string;
  filter_descriptions: string[];
  methodology_note: string;
}

interface Filter {
  field: string;
  op: string;
  value: number | string;
  compare_to_field?: string;
}

export default function ScannersPage() {
  const [scanners, setScanners] = useState<ScannerDef[]>([]);
  const [fields, setFields] = useState<string[]>([]);
  const [operators, setOperators] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [custom, setCustom] = useState<Filter[]>([{ field: "rsi_14", op: "<", value: 30 }]);
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.get<any>("/api/scanners", false).then((response) => {
      if (response.data) {
        setScanners(response.data.scanners);
        setFields(response.data.available_fields);
        setOperators(response.data.operators);
        setNote(response.data.note);
      }
    });
  }, []);

  const run = async (scannerKey: string | null) => {
    setLoading(true);
    setSelected(scannerKey);
    const response = await api.post<any>(
      "/api/scanners/run",
      scannerKey
        ? { scanner_key: scannerKey }
        : { filters: custom, logic: "AND" },
      false,
    );
    setResult(response.data ?? { error: response.error });
    setLoading(false);
  };

  const grouped = scanners.reduce<Record<string, ScannerDef[]>>((acc, scanner) => {
    (acc[scanner.category] ||= []).push(scanner);
    return acc;
  }, {});

  return (
    <div className="space-y-3">
      <Panel title="Scanners" subtitle={note ?? undefined}>
        <div className="space-y-4">
          {Object.entries(grouped).map(([category, items]) => (
            <div key={category}>
              <h3 className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-muted">
                {titleCase(category)}
              </h3>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((scanner) => (
                  <button
                    key={scanner.key}
                    onClick={() => run(scanner.key)}
                    className={
                      selected === scanner.key
                        ? "rounded border border-accent/50 bg-accent/10 p-2.5 text-left"
                        : "rounded border border-line bg-raised/30 p-2.5 text-left hover:border-line-strong"
                    }
                  >
                    <div className="text-xs font-medium text-ink">{scanner.name}</div>
                    <p className="mt-0.5 text-2xs leading-relaxed text-ink-muted">
                      {scanner.description}
                    </p>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {scanner.filter_descriptions.map((filter) => (
                        <span key={filter} className="num chip border-line bg-bg text-[10px] text-ink-muted">
                          {filter}
                        </span>
                      ))}
                    </div>
                    {scanner.methodology_note && (
                      <p className="mt-1.5 text-[10px] leading-relaxed text-warn/80">
                        {scanner.methodology_note}
                      </p>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Custom scan"
        subtitle="Combine any stored technical or fundamental field"
        actions={
          <button className="btn btn-accent" onClick={() => run(null)}>
            Run custom scan
          </button>
        }
      >
        <div className="space-y-2">
          {custom.map((filter, index) => (
            <div key={index} className="flex flex-wrap items-center gap-1.5">
              <select
                value={filter.field}
                onChange={(event) => {
                  const next = [...custom];
                  next[index] = { ...filter, field: event.target.value };
                  setCustom(next);
                }}
                className="field w-52"
                aria-label="Field"
              >
                {fields.map((field) => (
                  <option key={field} value={field}>{field}</option>
                ))}
              </select>
              <select
                value={filter.op}
                onChange={(event) => {
                  const next = [...custom];
                  next[index] = { ...filter, op: event.target.value };
                  setCustom(next);
                }}
                className="field w-24"
                aria-label="Operator"
              >
                {operators.map((operator) => (
                  <option key={operator} value={operator}>{operator}</option>
                ))}
              </select>
              <input
                value={String(filter.value)}
                onChange={(event) => {
                  const next = [...custom];
                  const parsed = Number(event.target.value);
                  next[index] = {
                    ...filter,
                    value: Number.isNaN(parsed) ? event.target.value : parsed,
                  };
                  setCustom(next);
                }}
                className="field w-28"
                aria-label="Value"
              />
              <select
                value={filter.compare_to_field || ""}
                onChange={(event) => {
                  const next = [...custom];
                  next[index] = {
                    ...filter,
                    compare_to_field: event.target.value || undefined,
                  };
                  setCustom(next);
                }}
                className="field w-52"
                aria-label="Compare to another field"
              >
                <option value="">— compare to a literal —</option>
                {fields.map((field) => (
                  <option key={field} value={field}>vs {field}</option>
                ))}
              </select>
              <button
                className="btn px-2 py-1"
                onClick={() => setCustom(custom.filter((_, i) => i !== index))}
                aria-label="Remove filter"
              >
                ✕
              </button>
            </div>
          ))}
          <button
            className="btn"
            onClick={() => setCustom([...custom, { field: "close", op: ">", value: 0, compare_to_field: "sma_50" }])}
          >
            Add filter
          </button>
        </div>
      </Panel>

      {loading && <Panel><Loading label="Scanning" /></Panel>}

      {!loading && result && (
        <Panel
          title={`Results — ${result.scanner_name || "Custom scan"}`}
          subtitle={
            result.as_of
              ? `${result.match_count} of ${result.universe_size} instruments · snapshot ${dateIST(result.as_of)}`
              : undefined
          }
          bodyClassName="p-0"
        >
          {result.error ? (
            <div className="p-4"><Unavailable reason={result.error} /></div>
          ) : (
            <>
              {result.warnings?.length > 0 && (
                <div className="space-y-1.5 border-b border-line p-3">
                  {result.warnings.map((warning: string) => (
                    <Notice key={warning} tone="warn">{warning}</Notice>
                  ))}
                </div>
              )}
              {result.matches?.length === 0 ? (
                <div className="p-4">
                  <Empty message="No instruments matched. Missing data reads as 'does not match', never as 'passes'." />
                </div>
              ) : (
                <div className="scroll-x">
                  <table className="w-full min-w-[820px]">
                    <thead className="border-b border-line">
                      <tr>
                        <th className="th">Symbol</th>
                        <th className="th">Sector</th>
                        <th className="th text-right">LTP</th>
                        <th className="th text-right">Change</th>
                        <th className="th text-right">RSI</th>
                        <th className="th text-right">Vol ×</th>
                        <th className="th text-right">ADX</th>
                        <th className="th text-right">ROE</th>
                        <th className="th text-right">P/E</th>
                        <th className="th">Matched values</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line/50">
                      {result.matches.map((match: any) => (
                        <tr key={match.symbol} className="hover:bg-raised/40">
                          <td className="td">
                            <Link href={`/stocks/${match.symbol}`} className="font-semibold text-ink hover:text-accent">
                              {match.symbol}
                            </Link>
                            {match.is_demo && <span className="ml-1 text-[9px] text-warn">DEMO</span>}
                          </td>
                          <td className="td max-w-[150px] truncate text-ink-muted">{match.sector || "—"}</td>
                          <td className="td num text-right">{num(match.ltp)}</td>
                          <td className={`td num text-right ${signClass(match.change_pct)}`}>
                            {pct(match.change_pct)}
                          </td>
                          <td className="td num text-right">{num(match.rsi_14, 1)}</td>
                          <td className="td num text-right">{num(match.volume_ratio_20d, 2)}</td>
                          <td className="td num text-right">{num(match.adx_14, 1)}</td>
                          <td className="td num text-right">{num(match.roe, 1)}</td>
                          <td className="td num text-right">{num(match.pe, 1)}</td>
                          <td className="td num max-w-[220px] truncate text-2xs text-ink-muted">
                            {Object.entries(match.matched_values || {})
                              .map(([key, value]) => `${key}=${value === null ? "n/a" : num(value as number, 2)}`)
                              .join(", ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {result.filters_applied?.length > 0 && (
                <p className="num border-t border-line px-3.5 py-2 text-2xs text-ink-muted">
                  Filters: {result.filters_applied.join(" AND ")}
                </p>
              )}
            </>
          )}
        </Panel>
      )}
    </div>
  );
}
