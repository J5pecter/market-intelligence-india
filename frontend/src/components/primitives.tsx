"use client";

import clsx from "clsx";
import { AlertTriangle, HelpCircle, Info, Loader2 } from "lucide-react";
import { ReactNode, useState } from "react";
import { DASH, num } from "@/lib/format";

/* -------------------------------------------------------------------------
 * Panel
 * ---------------------------------------------------------------------- */

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={clsx("card", className)}>
      {(title || actions) && (
        <header className="card-head">
          <div className="min-w-0">
            {title && <h2 className="card-title truncate">{title}</h2>}
            {subtitle && (
              <p className="mt-0.5 truncate text-2xs text-ink-muted">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={clsx("p-3.5", bodyClassName)}>{children}</div>
    </section>
  );
}

/* -------------------------------------------------------------------------
 * Data-status badge - the single most important component in the app.
 * Colour alone never carries the meaning; the label is always present.
 * ---------------------------------------------------------------------- */

const STATUS_STYLE: Record<string, string> = {
  LIVE: "border-pos/40 bg-pos/10 text-pos",
  DELAYED: "border-info/40 bg-info/10 text-info",
  STALE: "border-neutral2/40 bg-neutral2/10 text-ink-dim",
  UNAVAILABLE: "border-neg/40 bg-neg/10 text-neg",
  ESTIMATED: "border-warn/40 bg-warn/10 text-warn",
  MANUAL: "border-line-strong bg-raised text-ink-dim",
  UNVERIFIED: "border-warn/40 bg-warn/10 text-warn",
  DEMO: "border-warn/50 bg-warn/15 text-warn",
};

const STATUS_HELP: Record<string, string> = {
  LIVE: "Observed within this capability's freshness window.",
  DELAYED: "The provider is known to be delayed. Not an exchange feed.",
  STALE: "Older than the acceptable window. This is the last successful update, not live data.",
  UNAVAILABLE: "No provider returned a value.",
  ESTIMATED: "Derived or modelled - not observed.",
  MANUAL: "Entered by an operator through the admin panel.",
  UNVERIFIED: "Third-party aggregate that has not been cross-checked.",
  DEMO: "Seeded sample data shipped with the repository. Not market data.",
};

export function DataBadge({
  status,
  source,
  observedAt,
  compact = false,
}: {
  status?: string | null;
  source?: string | null;
  observedAt?: string | null;
  compact?: boolean;
}) {
  const key = (status || "UNAVAILABLE").toUpperCase();
  const title = [
    STATUS_HELP[key] || "Unknown data status.",
    source ? `Source: ${source}` : null,
    observedAt ? `Observed: ${observedAt}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <span
      className={clsx("chip", STATUS_STYLE[key] || STATUS_STYLE.UNAVAILABLE)}
      title={title}
      aria-label={`Data status: ${key}. ${STATUS_HELP[key] || ""}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {key}
      {!compact && source ? (
        <span className="hidden max-w-[16ch] truncate opacity-70 sm:inline">
          · {source}
        </span>
      ) : null}
    </span>
  );
}

/* -------------------------------------------------------------------------
 * Stat
 * ---------------------------------------------------------------------- */

export function Stat({
  label,
  value,
  hint,
  tone,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "pos" | "neg" | "warn" | "muted";
  className?: string;
}) {
  return (
    <div className={clsx("min-w-0", className)}>
      <div className="flex items-center gap-1 text-2xs uppercase tracking-wide text-ink-muted">
        <span className="truncate">{label}</span>
        {hint && <InfoDot text={hint} />}
      </div>
      <div
        className={clsx(
          "num mt-0.5 truncate text-sm font-semibold",
          tone === "pos" && "text-pos",
          tone === "neg" && "text-neg",
          tone === "warn" && "text-warn",
          tone === "muted" && "text-ink-dim",
        )}
      >
        {value}
      </div>
    </div>
  );
}

export function InfoDot({ text }: { text: string }) {
  return (
    <span className="relative inline-flex" title={text}>
      <HelpCircle className="h-3 w-3 text-ink-muted" aria-hidden />
      <span className="sr-only">{text}</span>
    </span>
  );
}

/* -------------------------------------------------------------------------
 * States
 * ---------------------------------------------------------------------- */

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-xs text-ink-muted">
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      {label}…
    </div>
  );
}

export function Unavailable({
  reason,
  hint,
  actions,
}: {
  reason?: string | null;
  hint?: string[] | null;
  actions?: ReactNode;
}) {
  return (
    <div className="rounded border border-dashed border-line-strong bg-raised/40 p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        <div className="min-w-0 space-y-2">
          <p className="text-xs font-medium text-ink">Data temporarily unavailable</p>
          {reason && <p className="text-2xs leading-relaxed text-ink-dim">{reason}</p>}
          {hint && hint.length > 0 && (
            <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
              {hint.map((h) => (
                <li key={h}>{h}</li>
              ))}
            </ul>
          )}
          {actions}
        </div>
      </div>
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return (
    <p className="py-6 text-center text-xs text-ink-muted">{message}</p>
  );
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "warn" | "neg";
  children: ReactNode;
}) {
  const Icon = tone === "info" ? Info : AlertTriangle;
  return (
    <div
      className={clsx(
        "flex items-start gap-2 rounded border p-2.5 text-2xs leading-relaxed",
        tone === "info" && "border-info/30 bg-info/5 text-info",
        tone === "warn" && "border-warn/30 bg-warn/5 text-warn",
        tone === "neg" && "border-neg/30 bg-neg/5 text-neg",
      )}
      role={tone === "info" ? "note" : "alert"}
    >
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Score bar
 * ---------------------------------------------------------------------- */

export function ScoreBar({
  label,
  score,
  stance,
  note,
  inverted = false,
}: {
  label: string;
  score: number | null | undefined;
  stance?: string | null;
  note?: string | null;
  inverted?: boolean;
}) {
  const available = score !== null && score !== undefined;
  const tone = !available
    ? "bg-neutral2/40"
    : inverted
      ? score >= 55
        ? "bg-neg"
        : score >= 35
          ? "bg-warn"
          : "bg-pos"
      : score >= 60
        ? "bg-pos"
        : score >= 40
          ? "bg-warn"
          : "bg-neg";

  return (
    <div className="space-y-1" title={note || undefined}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-2xs uppercase tracking-wide text-ink-dim">
          {label}
        </span>
        <span className="num text-2xs font-semibold text-ink">
          {available ? num(score, 0) : "n/a"}
          {available && <span className="text-ink-muted">/100</span>}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
        <div
          className={clsx("h-full rounded-full transition-all", tone)}
          style={{ width: available ? `${Math.max(2, Math.min(100, score!))}%` : "100%" }}
          aria-hidden
        />
      </div>
      {!available && (
        <p className="text-2xs text-ink-muted">
          Not scored — {note || "no inputs were available"}.
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Disclosure
 * ---------------------------------------------------------------------- */

export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  count,
}: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  count?: number;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded border border-line bg-raised/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-ink hover:bg-raised"
      >
        <span className="min-w-0 truncate">{summary}</span>
        <span className="shrink-0 text-2xs text-ink-muted">
          {count !== undefined && `${count} · `}
          {open ? "Hide" : "Show"}
        </span>
      </button>
      {open && <div className="border-t border-line px-3 py-2.5">{children}</div>}
    </div>
  );
}

export function Tag({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "pos" | "neg" | "warn" | "info" | "accent";
}) {
  return (
    <span
      className={clsx(
        "chip",
        tone === "neutral" && "border-line-strong bg-raised text-ink-dim",
        tone === "pos" && "border-pos/40 bg-pos/10 text-pos",
        tone === "neg" && "border-neg/40 bg-neg/10 text-neg",
        tone === "warn" && "border-warn/40 bg-warn/10 text-warn",
        tone === "info" && "border-info/40 bg-info/10 text-info",
        tone === "accent" && "border-accent/40 bg-accent/10 text-accent",
      )}
    >
      {children}
    </span>
  );
}
