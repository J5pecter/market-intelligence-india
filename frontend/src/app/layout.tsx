import type { Metadata } from "next";
import { ReactNode } from "react";
import { Shell } from "@/components/Shell";
import { apiFetch } from "@/lib/api";
import "./globals.css";

/**
 * Branding and compliance are fetched server-side on every render so a config
 * change takes effect without a rebuild, and so the disclaimer wording can
 * never drift from what the backend actually permits.
 */

const FALLBACK_BRANDING = {
  name: "Market Intelligence India",
  short_name: "MII",
  tagline: "Evidence-first research for Indian markets",
  logo_mark_text: "MI",
  footer_text:
    "Market data is delayed or sourced from public endpoints unless a licensed provider is configured.",
  colors: {} as Record<string, string>,
  typography: {} as Record<string, string>,
};

const FALLBACK_COMPLIANCE = {
  descriptor: "Educational / informational market research platform",
  is_registered: false,
  verification_badge: null,
  disclaimers: {
    primary:
      "Information displayed on this platform is for informational and educational purposes only and should not be construed as investment advice.",
    derivatives: "",
  },
  review_overdue: true,
};

export const metadata: Metadata = {
  title: "Market Intelligence India",
  description:
    "Evidence-first research and market-intelligence terminal for NSE and BSE. Not investment advice.",
  robots: { index: false, follow: false },
};

const HEX_TO_RGB = (hex?: string) => {
  if (!hex || !/^#([0-9a-f]{6})$/i.test(hex)) return null;
  const value = hex.slice(1);
  return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16)).join(" ");
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const [brandingResult, complianceResult] = await Promise.all([
    apiFetch<typeof FALLBACK_BRANDING>("/api/config/branding", { auth: false }),
    apiFetch<typeof FALLBACK_COMPLIANCE>("/api/config/compliance", { auth: false }),
  ]);

  const branding = brandingResult.data ?? FALLBACK_BRANDING;
  const compliance = complianceResult.data ?? FALLBACK_COMPLIANCE;

  // Brand colours become CSS custom properties - no component hard-codes one.
  const colours = branding.colors || {};
  const cssVars: Record<string, string> = {};
  const map: Record<string, string> = {
    background: "--c-bg",
    surface: "--c-surface",
    surface_raised: "--c-raised",
    border: "--c-border",
    border_strong: "--c-border-strong",
    text_primary: "--c-text",
    text_secondary: "--c-text-2",
    text_muted: "--c-text-3",
    accent: "--c-accent",
    accent_muted: "--c-accent-soft",
    positive: "--c-pos",
    negative: "--c-neg",
    warning: "--c-warn",
    info: "--c-info",
    neutral: "--c-neutral",
  };
  for (const [key, variable] of Object.entries(map)) {
    const rgb = HEX_TO_RGB(colours[key]);
    if (rgb) cssVars[variable] = rgb;
  }
  const typography = (branding.typography || {}) as Record<string, string>;
  if (typography.sans) cssVars["--font-sans"] = typography.sans;
  if (typography.mono) cssVars["--font-mono"] = typography.mono;

  const offline = brandingResult.error || complianceResult.error;

  return (
    <html lang="en-IN" style={cssVars as never}>
      <body>
        {offline && (
          <div
            role="alert"
            className="bg-neg/15 px-3 py-1.5 text-center text-2xs text-neg"
          >
            The API is unreachable ({offline}). Panels below will show
            &ldquo;temporarily unavailable&rdquo; rather than stale numbers.
          </div>
        )}
        <Shell branding={branding as never} compliance={compliance as never}>
          {children}
        </Shell>
      </body>
    </html>
  );
}
