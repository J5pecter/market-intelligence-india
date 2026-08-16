"use client";

import clsx from "clsx";
import { BarChart3, Bell, ExternalLink, Eye, LineChart, Star } from "lucide-react";
import Link from "next/link";
import { Tag } from "@/components/primitives";
import { DASH, dateTimeIST, inr, num, pct, signClass } from "@/lib/format";

/**
 * The research card.
 *
 * Three things it will never do:
 *  1. Show a permanent "BUY" - the badge is the *status*, recomputed from live
 *     price by the backend on every read.
 *  2. Present a third-party call as our own - the source block is mandatory.
 *  3. Show a percentage without saying what it is measured from.
 */

export interface ResearchCallCard {
  id: string;
  symbol: string;
  company: string;
  segment: string;
  expiry: string | null;
  strike: number | null;
  option_type: string | null;
  lot_size: number | null;
  side: string;
  source_type: string;
  source: {
    name: string;
    analyst: string | null;
    url: string | null;
    organisation: string | null;
    reliability: string;
    registration_note: string | null;
    published_at: string | null;
    valid_until: string | null;
    was_transformed: boolean;
    transformation_note: string | null;
    original_recommendation: string | null;
    attribution_notice: string;
  };
  ltp: number | null;
  entry_min: number | null;
  entry_max: number | null;
  stop_loss: number | null;
  targets: number[];
  status: string;
  status_reason: string | null;
  lifecycle_state: string;
  achieved_pct: number | null;
  potential_pct: number | null;
  potential_label: string;
  risk_reward: number | null;
  risk_rating: string | null;
  confidence: number | null;
  horizon: string | null;
  version: number;
  updated_at: string | null;
  rationale: string | null;
  invalidation: string | null;
  is_demo: boolean;
  data_status: string;
  evaluation?: {
    warnings?: string[];
    formulas?: Record<string, string>;
    targets?: Array<{
      index: number;
      price: number;
      return_from_entry_pct: number;
      r_multiple: number | null;
      reached: boolean;
    }>;
  } | null;
}

const STATUS_TONE: Record<string, string> = {
  WITHIN_ENTRY: "border-pos/50 bg-pos/10 text-pos",
  TARGET_IN_PROGRESS: "border-info/50 bg-info/10 text-info",
  TARGET_ACHIEVED: "border-pos/50 bg-pos/15 text-pos",
  STOP_LOSS_TRIGGERED: "border-neg/50 bg-neg/15 text-neg",
  NOT_ACTIVATED: "border-warn/40 bg-warn/10 text-warn",
  ABOVE_ENTRY: "border-warn/40 bg-warn/10 text-warn",
  BELOW_ENTRY: "border-warn/40 bg-warn/10 text-warn",
  EXPIRED: "border-neutral2/40 bg-neutral2/10 text-ink-dim",
  INVALIDATED: "border-neutral2/40 bg-neutral2/10 text-ink-dim",
  UNKNOWN: "border-neutral2/40 bg-neutral2/10 text-ink-muted",
};

const STATUS_LABEL: Record<string, string> = {
  WITHIN_ENTRY: "Within entry range",
  TARGET_IN_PROGRESS: "Moving toward target",
  TARGET_ACHIEVED: "Target reached",
  STOP_LOSS_TRIGGERED: "Stop loss reached",
  NOT_ACTIVATED: "Not activated",
  ABOVE_ENTRY: "Above entry range",
  BELOW_ENTRY: "Below entry range",
  EXPIRED: "Expired",
  INVALIDATED: "Invalidated",
  UNKNOWN: "Status unavailable",
};

export function ResearchCard({ call }: { call: ResearchCallCard }) {
  const isOption = call.segment === "OPTION";
  const target = call.targets[call.targets.length - 1] ?? null;
  const contract = isOption
    ? `${call.expiry ? new Date(call.expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" }) : ""} ${num(call.strike, 2)} ${call.option_type}`
    : call.company;

  return (
    <article className="card flex flex-col">
      {/* header */}
      <header className="flex items-start justify-between gap-2 border-b border-line px-3.5 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <Tag tone={call.side === "BUY" ? "pos" : call.side === "SELL" ? "neg" : "warn"}>
              {call.side}
            </Tag>
            <h3 className="truncate text-sm font-bold tracking-wide text-ink">
              {call.symbol}
            </h3>
            {call.is_demo && <Tag tone="warn">DEMO</Tag>}
          </div>
          <p className="mt-0.5 truncate text-2xs text-ink-dim">{contract}</p>
        </div>
        <div className="shrink-0 text-right">
          <Tag tone={call.source_type === "EXTERNAL_RESEARCH" ? "info" : "accent"}>
            {call.source_type === "EXTERNAL_RESEARCH" ? "Third-party" : "Platform"}
          </Tag>
        </div>
      </header>

      {/* price + status */}
      <div className="flex items-start justify-between gap-3 px-3.5 py-3">
        <div className="min-w-0">
          <span className="text-2xs uppercase tracking-wide text-ink-muted">
            {isOption ? "Premium" : "LTP"}
          </span>
          <div className="num text-lg font-bold text-accent">{inr(call.ltp)}</div>
        </div>
        <div className="min-w-0 text-right">
          <span className="text-2xs uppercase tracking-wide text-ink-muted">
            Recommended price
          </span>
          <div className="num text-xs font-medium text-ink">
            {call.entry_min === call.entry_max
              ? inr(call.entry_min)
              : `${inr(call.entry_min)} – ${inr(call.entry_max)}`}
          </div>
        </div>
      </div>

      <div className="px-3.5">
        <span
          className={clsx(
            "chip w-full justify-center",
            STATUS_TONE[call.status] || STATUS_TONE.UNKNOWN,
          )}
          title={call.status_reason || undefined}
        >
          {STATUS_LABEL[call.status] || call.status}
        </span>
        {call.status_reason && (
          <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
            {call.status_reason}
          </p>
        )}
      </div>

      {/* SL - Entry - Target gauge */}
      <LevelGauge
        stopLoss={call.stop_loss}
        entryMin={call.entry_min}
        entryMax={call.entry_max}
        target={target}
        ltp={call.ltp}
        side={call.side}
      />

      {/* achieved / potential */}
      <div className="mx-3.5 grid grid-cols-2 overflow-hidden rounded border border-line text-2xs font-semibold">
        <div
          className={clsx(
            "px-2 py-1.5 text-center",
            (call.achieved_pct ?? 0) >= 0
              ? "bg-pos/10 text-pos"
              : "bg-neg/10 text-neg",
          )}
          title="Achieved = (last price − entry reference) / entry reference × 100. The entry reference is the worst end of the published range for the direction traded."
        >
          ACHIEVED {pct(call.achieved_pct)}
        </div>
        <div
          className="border-l border-line bg-warn/5 px-2 py-1.5 text-center text-warn"
          title={
            isOption
              ? "Potential left = (target − last premium) / last premium × 100."
              : "Potential expected = (target − entry reference) / entry reference × 100."
          }
        >
          {pct(call.potential_pct)} {call.potential_label.toUpperCase()}
        </div>
      </div>

      {/* risk row */}
      <dl className="grid grid-cols-3 gap-2 px-3.5 py-3 text-center">
        <div>
          <dt className="text-2xs uppercase text-ink-muted">Risk / reward</dt>
          <dd className="num text-xs font-semibold text-ink">
            {call.risk_reward ? `1 : ${num(call.risk_reward, 2)}` : DASH}
          </dd>
        </div>
        <div>
          <dt className="text-2xs uppercase text-ink-muted">Risk</dt>
          <dd
            className={clsx(
              "text-xs font-semibold",
              call.risk_rating === "LOW" && "text-pos",
              call.risk_rating === "MODERATE" && "text-warn",
              (call.risk_rating === "HIGH" || call.risk_rating === "VERY_HIGH") &&
                "text-neg",
              !call.risk_rating && "text-ink-muted",
            )}
          >
            {call.risk_rating ? call.risk_rating.replace("_", " ") : "Not rated"}
          </dd>
        </div>
        <div>
          <dt className="text-2xs uppercase text-ink-muted">Confidence</dt>
          <dd className="num text-xs font-semibold text-ink">
            {call.confidence !== null ? `${num(call.confidence, 0)}/100` : DASH}
          </dd>
        </div>
      </dl>

      {/* provenance - never optional */}
      <div className="mx-3.5 mb-3 rounded border border-line bg-raised/50 px-2.5 py-2 text-2xs">
        <p className="text-ink-dim">
          <span className="text-ink-muted">Source:</span> {call.source.name}
          {call.source.analyst ? ` · ${call.source.analyst}` : ""}
        </p>
        {call.source.published_at && (
          <p className="mt-0.5 text-ink-muted">
            Published {dateTimeIST(call.source.published_at)}
            {call.source.valid_until
              ? ` · valid until ${dateTimeIST(call.source.valid_until)}`
              : ""}
          </p>
        )}
        {call.source.was_transformed && (
          <p className="mt-0.5 text-warn">
            Modified from the original: {call.source.transformation_note}
          </p>
        )}
        <p className="mt-1 leading-relaxed text-ink-muted">
          {call.source.attribution_notice}
        </p>
      </div>

      {/* actions */}
      <footer className="mt-auto flex items-center justify-between gap-2 border-t border-line px-3.5 py-2.5">
        <span className="text-2xs text-ink-muted">
          v{call.version} · updated {dateTimeIST(call.updated_at)}
        </span>
        <div className="flex items-center gap-1">
          <Link
            href={`/research/${call.id}`}
            className="btn px-2 py-1"
            title="Open the full evidence chain"
          >
            <Eye className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">Research</span>
          </Link>
          <Link
            href={`/stocks/${call.symbol}`}
            className="btn px-2 py-1"
            title="Open the instrument"
          >
            <LineChart className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">Chart</span>
          </Link>
          {isOption && (
            <Link
              href={`/fno/options?symbol=${call.symbol}`}
              className="btn px-2 py-1"
              title="Open the option chain"
            >
              <BarChart3 className="h-3.5 w-3.5" aria-hidden />
              <span className="sr-only">Options</span>
            </Link>
          )}
          <Link
            href={`/alerts?symbol=${call.symbol}&call=${call.id}`}
            className="btn px-2 py-1"
            title="Create an alert"
          >
            <Bell className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">Alert</span>
          </Link>
        </div>
      </footer>
    </article>
  );
}

/* -------------------------------------------------------------------------
 * The SL → Entry → Target rail, with the live price plotted on it.
 * ---------------------------------------------------------------------- */

function LevelGauge({
  stopLoss,
  entryMin,
  entryMax,
  target,
  ltp,
  side,
}: {
  stopLoss: number | null;
  entryMin: number | null;
  entryMax: number | null;
  target: number | null;
  ltp: number | null;
  side: string;
}) {
  const entry = entryMax ?? entryMin;
  const points = [stopLoss, entry, target].filter(
    (p): p is number => p !== null && p !== undefined,
  );
  const hasScale = points.length >= 2 && ltp !== null;

  const low = hasScale ? Math.min(...points, ltp!) : 0;
  const high = hasScale ? Math.max(...points, ltp!) : 1;
  const span = high - low || 1;
  const position = (value: number | null) =>
    value === null ? null : ((value - low) / span) * 100;

  return (
    <div className="px-3.5 pb-1 pt-2">
      <div className="relative h-9">
        <div
          className="absolute inset-x-1 top-4 h-px border-t border-dashed border-line-strong"
          aria-hidden
        />
        {hasScale && (
          <>
            <GaugeNode
              left={position(stopLoss)}
              label="SL"
              tone="neg"
              value={stopLoss}
            />
            <GaugeNode left={position(entry)} label="B" tone="accent" value={entry} />
            <GaugeNode left={position(target)} label="S" tone="warn" value={target} />
            {ltp !== null && (
              <div
                className="absolute top-0 -translate-x-1/2"
                style={{ left: `${Math.max(2, Math.min(98, position(ltp)!))}%` }}
                title={`Last price ${inr(ltp)}`}
              >
                <div className="h-8 w-px bg-accent" aria-hidden />
                <span className="sr-only">Last price {ltp}</span>
              </div>
            )}
          </>
        )}
      </div>
      <div className="grid grid-cols-3 gap-1 text-2xs">
        <div>
          <div className="text-ink-muted">Stop loss</div>
          <div className="num font-semibold text-neg">{inr(stopLoss)}</div>
        </div>
        <div className="text-center">
          <div className="text-ink-muted">Entry</div>
          <div className="num font-semibold text-ink">
            {entryMin === entryMax ? inr(entryMin) : `${num(entryMin)}–${num(entryMax)}`}
          </div>
        </div>
        <div className="text-right">
          <div className="text-ink-muted">Target</div>
          <div className="num font-semibold text-pos">{inr(target)}</div>
        </div>
      </div>
    </div>
  );
}

function GaugeNode({
  left,
  label,
  tone,
  value,
}: {
  left: number | null;
  label: string;
  tone: "neg" | "accent" | "warn";
  value: number | null;
}) {
  if (left === null) return null;
  return (
    <span
      className={clsx(
        "absolute top-1.5 flex h-5 w-5 -translate-x-1/2 items-center justify-center rounded-full border text-[9px] font-bold",
        tone === "neg" && "border-neg bg-neg/15 text-neg",
        tone === "accent" && "border-accent bg-accent/15 text-accent",
        tone === "warn" && "border-warn bg-warn/15 text-warn",
      )}
      style={{ left: `${Math.max(3, Math.min(97, left))}%` }}
      title={value !== null ? inr(value) : undefined}
    >
      {label}
    </span>
  );
}
