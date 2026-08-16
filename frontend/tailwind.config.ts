import type { Config } from "tailwindcss";

/**
 * Colours are CSS custom properties, not literals. The values are injected at
 * runtime from `/api/config/branding`, so an operator can rebrand the whole
 * terminal without touching a component.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--c-bg) / <alpha-value>)",
        surface: "rgb(var(--c-surface) / <alpha-value>)",
        raised: "rgb(var(--c-raised) / <alpha-value>)",
        line: "rgb(var(--c-border) / <alpha-value>)",
        "line-strong": "rgb(var(--c-border-strong) / <alpha-value>)",
        ink: "rgb(var(--c-text) / <alpha-value>)",
        "ink-dim": "rgb(var(--c-text-2) / <alpha-value>)",
        "ink-muted": "rgb(var(--c-text-3) / <alpha-value>)",
        accent: "rgb(var(--c-accent) / <alpha-value>)",
        "accent-soft": "rgb(var(--c-accent-soft) / <alpha-value>)",
        pos: "rgb(var(--c-pos) / <alpha-value>)",
        neg: "rgb(var(--c-neg) / <alpha-value>)",
        warn: "rgb(var(--c-warn) / <alpha-value>)",
        info: "rgb(var(--c-info) / <alpha-value>)",
        neutral2: "rgb(var(--c-neutral) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "0.95rem" }],
        xs: ["0.75rem", { lineHeight: "1.05rem" }],
        sm: ["0.8125rem", { lineHeight: "1.15rem" }],
        base: ["0.875rem", { lineHeight: "1.3rem" }],
      },
      borderRadius: { DEFAULT: "0.375rem", lg: "0.5rem", xl: "0.75rem" },
      keyframes: {
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
      animation: { pulseSoft: "pulseSoft 2.2s ease-in-out infinite" },
    },
  },
  plugins: [],
};

export default config;
