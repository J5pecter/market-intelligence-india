"use client";

import { Search, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Tag } from "@/components/primitives";
import { api } from "@/lib/api";
import { num, pct, signClass } from "@/lib/format";

interface SearchResult {
  symbol: string;
  display_name: string;
  name: string;
  segment: string;
  exchange: string;
  isin: string | null;
  nse_code: string | null;
  bse_code: string | null;
  sector: string | null;
  ltp: number | null;
  change_pct: number | null;
  is_demo: boolean;
  actions: Array<{ label: string; href: string }>;
}

export function SearchDialog({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Debounced so typing does not hammer the API.
  useEffect(() => {
    if (query.trim().length < 1) {
      setResults([]);
      setNote(null);
      return;
    }
    const timer = setTimeout(async () => {
      setLoading(true);
      const response = await api.get<{
        results: SearchResult[];
        note: string | null;
      }>(`/api/search?q=${encodeURIComponent(query.trim())}`, false);
      setLoading(false);
      if (response.data) {
        setResults(response.data.results);
        setNote(response.data.note);
      } else {
        setResults([]);
        setNote(response.error);
      }
    }, 220);
    return () => clearTimeout(timer);
  }, [query]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 p-4 pt-[10vh] backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Search instruments"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl overflow-hidden rounded-lg border border-line-strong bg-surface shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
          <Search className="h-4 w-4 text-ink-muted" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Symbol, company, ISIN, NSE/BSE code…"
            className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-muted"
            aria-label="Search query"
          />
          <button className="btn px-2 py-1" onClick={onClose}>
            <X className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">Close</span>
          </button>
        </div>

        <div className="max-h-[60vh] overflow-y-auto">
          {loading && (
            <p className="px-3 py-4 text-xs text-ink-muted">Searching…</p>
          )}
          {!loading && query && results.length === 0 && (
            <p className="px-3 py-4 text-xs text-ink-muted">
              Nothing matched “{query}”. The search covers the instrument master
              stored in this deployment — run the instrument sync job to widen it.
            </p>
          )}
          <ul className="divide-y divide-line/60">
            {results.map((result) => (
              <li key={`${result.symbol}-${result.segment}`} className="px-3 py-2.5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-bold text-ink">
                        {result.symbol}
                      </span>
                      <Tag tone="neutral">{result.segment}</Tag>
                      <span className="text-2xs text-ink-muted">
                        {result.exchange}
                      </span>
                      {result.is_demo && <Tag tone="warn">DEMO</Tag>}
                    </div>
                    <p className="truncate text-2xs text-ink-dim">{result.name}</p>
                    <p className="mt-0.5 text-2xs text-ink-muted">
                      {result.nse_code && `NSE: ${result.nse_code}`}
                      {result.bse_code && ` · BSE: ${result.bse_code}`}
                      {result.isin && ` · ${result.isin}`}
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="num text-xs font-semibold text-ink">
                      {num(result.ltp)}
                    </div>
                    <div className={`num text-2xs ${signClass(result.change_pct)}`}>
                      {pct(result.change_pct)}
                    </div>
                  </div>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {result.actions.map((action) => (
                    <Link
                      key={action.href}
                      href={action.href}
                      onClick={onClose}
                      className="chip border-line bg-raised text-ink-dim hover:border-accent/50 hover:text-accent"
                    >
                      {action.label}
                    </Link>
                  ))}
                </div>
              </li>
            ))}
          </ul>
          {note && (
            <p className="border-t border-line px-3 py-2 text-2xs text-ink-muted">
              {note}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
