"use client";

import clsx from "clsx";
import { Minus, TrendingDown, TrendingUp } from "lucide-react";
import { Disclosure, ScoreBar, Tag } from "@/components/primitives";
import { DASH, dateTimeIST } from "@/lib/format";

/**
 * Renders an evidence chain exactly as the backend produced it:
 * metric -> value -> calculation -> interpretation -> source -> timestamp.
 *
 * This component contains no domain logic. If a number appears on screen, the
 * backend supplied the reasoning next to it.
 */

export interface EvidenceItem {
  metric: string;
  value: number | string | null;
  stance: string;
  weight: number;
  contribution: number | null;
  calculation: string | null;
  interpretation: string | null;
  source: string | null;
  source_url: string | null;
  observed_at: string | null;
  data_status: string | null;
  unit: string | null;
}

export interface EvidenceChain {
  dimension: string;
  score: number | null;
  stance: string;
  summary: string;
  evidence: EvidenceItem[];
  counter_evidence: EvidenceItem[];
  limitations: string[];
  data_gaps: string[];
  methodology: string | null;
  computed_at: string;
  item_count: number;
}

function StanceIcon({ stance }: { stance: string }) {
  if (stance === "POSITIVE") return <TrendingUp className="h-3 w-3 text-pos" aria-hidden />;
  if (stance === "NEGATIVE") return <TrendingDown className="h-3 w-3 text-neg" aria-hidden />;
  return <Minus className="h-3 w-3 text-ink-muted" aria-hidden />;
}

export function EvidenceRow({ item }: { item: EvidenceItem }) {
  return (
    <li className="border-b border-line/60 py-2 last:border-0">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">
          <StanceIcon stance={item.stance} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="text-xs font-medium text-ink">{item.metric}</span>
            <span className="num text-xs text-accent">
              {item.value === null ? DASH : String(item.value)}
              {item.unit && item.unit !== "INR" ? ` ${item.unit}` : ""}
            </span>
            <span className="text-2xs text-ink-muted">weight {item.weight}</span>
          </div>

          {item.interpretation && (
            <p className="mt-0.5 text-2xs leading-relaxed text-ink-dim">
              {item.interpretation}
            </p>
          )}

          {item.calculation && (
            <p className="num mt-1 rounded bg-bg px-1.5 py-1 text-2xs text-ink-muted">
              {item.calculation}
            </p>
          )}

          <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-2xs text-ink-muted">
            {item.source && (
              <span>
                Source:{" "}
                {item.source_url ? (
                  <a
                    href={item.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent underline underline-offset-2"
                  >
                    {item.source}
                  </a>
                ) : (
                  item.source
                )}
              </span>
            )}
            {item.data_status && <span>Status: {item.data_status}</span>}
            {item.observed_at && <span>Observed: {dateTimeIST(item.observed_at)}</span>}
          </div>
        </div>
      </div>
    </li>
  );
}

export function EvidencePanel({
  chain,
  defaultOpen = false,
}: {
  chain: EvidenceChain | null | undefined;
  defaultOpen?: boolean;
}) {
  if (!chain) return null;

  const stanceTone =
    chain.stance === "POSITIVE"
      ? "pos"
      : chain.stance === "NEGATIVE"
        ? "neg"
        : chain.stance === "NEUTRAL"
          ? "warn"
          : "neutral";

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Tag tone={stanceTone as never}>{chain.dimension}</Tag>
        <Tag tone="neutral">{chain.stance}</Tag>
        {chain.methodology && (
          <a
            href={chain.methodology}
            className="text-2xs text-accent underline underline-offset-2"
          >
            Methodology
          </a>
        )}
      </div>

      <ScoreBar label={`${chain.dimension} score`} score={chain.score} />

      {chain.summary && (
        <p className="text-xs leading-relaxed text-ink-dim">{chain.summary}</p>
      )}

      <Disclosure
        summary="Why? — the evidence behind this reading"
        count={chain.item_count}
        defaultOpen={defaultOpen}
      >
        <div className="space-y-4">
          <section>
            <h4 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-pos">
              Supporting evidence
            </h4>
            {chain.evidence.length ? (
              <ul>
                {chain.evidence.map((item, index) => (
                  <EvidenceRow key={`${item.metric}-${index}`} item={item} />
                ))}
              </ul>
            ) : (
              <p className="text-2xs text-ink-muted">
                None. No metric supported this direction.
              </p>
            )}
          </section>

          <section>
            <h4 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-neg">
              Counter-evidence
            </h4>
            {chain.counter_evidence.length ? (
              <ul>
                {chain.counter_evidence.map((item, index) => (
                  <EvidenceRow key={`${item.metric}-${index}`} item={item} />
                ))}
              </ul>
            ) : (
              <p className="text-2xs text-ink-muted">
                None found — which is worth treating with suspicion rather than
                comfort.
              </p>
            )}
          </section>

          {chain.data_gaps.length > 0 && (
            <section>
              <h4 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-warn">
                Blind spots
              </h4>
              <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-dim">
                {chain.data_gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </section>
          )}

          {chain.limitations.length > 0 && (
            <section>
              <h4 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-muted">
                Limitations
              </h4>
              <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
                {chain.limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
            </section>
          )}

          <p className="text-2xs text-ink-muted">
            Computed {dateTimeIST(chain.computed_at)} IST.
          </p>
        </div>
      </Disclosure>
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Why now / Why not - both mandatory on every setup.
 * ---------------------------------------------------------------------- */

export interface WhyItem {
  dimension: string;
  point: string;
  metric: string | null;
  value: number | string | null;
  calculation: string | null;
  source: string | null;
  observed_at: string | null;
  weight: number;
}

export function WhyPanels({
  whyNow,
  whyNot,
}: {
  whyNow: WhyItem[];
  whyNot: WhyItem[];
}) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <WhyList
        title="Why now?"
        tone="pos"
        items={whyNow}
        empty="No positive evidence cleared the threshold."
      />
      <WhyList
        title="Why this may fail"
        tone="neg"
        items={whyNot}
        empty="No counter-evidence surfaced."
      />
    </div>
  );
}

function WhyList({
  title,
  tone,
  items,
  empty,
}: {
  title: string;
  tone: "pos" | "neg";
  items: WhyItem[];
  empty: string;
}) {
  return (
    <div className="card">
      <div className="card-head">
        <h3
          className={clsx(
            "card-title",
            tone === "pos" ? "text-pos" : "text-neg",
          )}
        >
          {title}
        </h3>
        <span className="text-2xs text-ink-muted">{items.length}</span>
      </div>
      <ul className="divide-y divide-line/60">
        {items.length === 0 && (
          <li className="px-3.5 py-3 text-2xs text-ink-muted">{empty}</li>
        )}
        {items.map((item, index) => (
          <li key={`${item.point}-${index}`} className="px-3.5 py-2.5">
            <div className="flex items-start gap-2">
              <span
                className={clsx(
                  "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
                  tone === "pos" ? "bg-pos" : "bg-neg",
                )}
                aria-hidden
              />
              <div className="min-w-0">
                <p className="text-xs leading-relaxed text-ink">{item.point}</p>
                <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-2xs text-ink-muted">
                  <span>{item.dimension}</span>
                  {item.calculation && <span>· {item.calculation}</span>}
                  {item.source && <span>· {item.source}</span>}
                </div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
