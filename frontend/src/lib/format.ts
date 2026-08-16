/** Display helpers. Every one of them renders `null` as an em dash rather than
 *  a zero - a missing number must never look like a measured zero. */

export const DASH = "—";

export function num(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function inr(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return `₹${num(value, digits)}`;
}

export function pct(
  value: number | null | undefined,
  digits = 2,
  signed = true,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${num(value, digits)}%`;
}

/** Indian crore/lakh scaling - the units the market actually quotes in. */
export function compactInr(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const abs = Math.abs(value);
  if (abs >= 1e7) return `₹${num(value / 1e7, 2)} Cr`;
  if (abs >= 1e5) return `₹${num(value / 1e5, 2)} L`;
  if (abs >= 1e3) return `₹${num(value / 1e3, 1)} K`;
  return `₹${num(value, 2)}`;
}

export function compactNum(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const abs = Math.abs(value);
  if (abs >= 1e7) return `${num(value / 1e7, 2)} Cr`;
  if (abs >= 1e5) return `${num(value / 1e5, 2)} L`;
  if (abs >= 1e3) return `${num(value / 1e3, 1)} K`;
  return num(value, 0);
}

export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "text-ink-muted";
  }
  if (value > 0) return "text-pos";
  if (value < 0) return "text-neg";
  return "text-ink-dim";
}

export function dateTimeIST(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return DASH;
  return parsed.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

export function dateIST(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return DASH;
  return parsed.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function relativeAge(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const parsed = new Date(iso).getTime();
  if (Number.isNaN(parsed)) return DASH;
  const seconds = Math.floor((Date.now() - parsed) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return DASH;
  return value
    .replace(/[_-]+/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
