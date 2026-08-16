"use client";

import {
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  LineStyle,
  createChart,
} from "lightweight-charts";
import { Maximize2, Minimize2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { Loading, Unavailable } from "@/components/primitives";

/**
 * TradingView Lightweight Charts (Apache-2.0). Free, no account, no key.
 *
 * Overlays are driven by data the backend already computed - this component
 * never calculates an indicator itself.
 */

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface ChartPayload {
  available: boolean;
  reason?: string;
  candles: Candle[];
  indicators: Record<string, Array<{ time: number; value: number }>>;
  available_indicators: string[];
  provenance?: { source: string; status: string };
}

export interface PriceLine {
  price: number;
  label: string;
  colour: "pos" | "neg" | "accent" | "warn";
}

const OVERLAY_COLOURS: Record<string, string> = {
  sma_20: "#38bdf8",
  sma_50: "#f59e0b",
  sma_100: "#a78bfa",
  sma_200: "#f43f5e",
  ema_9: "#22d3ee",
  ema_20: "#2dd4bf",
  ema_50: "#fb923c",
  vwap: "#c084fc",
  bb_upper: "#64748b",
  bb_lower: "#64748b",
  supertrend: "#22c55e",
};

const PANE_INDICATORS = new Set(["rsi_14", "macd", "macd_hist", "adx_14", "atr_14"]);

export function PriceChart({
  data,
  overlays,
  priceLines = [],
  height = 420,
  loading = false,
}: {
  data: ChartPayload | null;
  overlays: string[];
  priceLines?: PriceLine[];
  height?: number;
  loading?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const cssVar = (name: string, fallback: string) => {
    if (typeof window === "undefined") return fallback;
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value ? `rgb(${value.split(" ").join(",")})` : fallback;
  };

  useEffect(() => {
    if (!containerRef.current || !data?.available || !data.candles.length) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: fullscreen ? window.innerHeight - 120 : height,
      layout: {
        background: { type: ColorType.Solid, color: cssVar("--c-surface", "#0e131b") },
        textColor: cssVar("--c-text-2", "#8fa1b8"),
        fontSize: 11,
        fontFamily: "var(--font-mono)",
      },
      grid: {
        vertLines: { color: cssVar("--c-border", "#1e2836") },
        horzLines: { color: cssVar("--c-border", "#1e2836") },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: cssVar("--c-border", "#1e2836"),
        scaleMargins: { top: 0.08, bottom: 0.28 },
      },
      timeScale: {
        borderColor: cssVar("--c-border", "#1e2836"),
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    });
    chartRef.current = chart;

    const candles = chart.addCandlestickSeries({
      upColor: cssVar("--c-pos", "#22c55e"),
      downColor: cssVar("--c-neg", "#f43f5e"),
      borderVisible: false,
      wickUpColor: cssVar("--c-pos", "#22c55e"),
      wickDownColor: cssVar("--c-neg", "#f43f5e"),
    });
    candles.setData(
      data.candles.map((c) => ({
        time: c.time as never,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );
    candleRef.current = candles;

    const withVolume = data.candles.filter((c) => c.volume !== null);
    if (withVolume.length) {
      const volume = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      volume.setData(
        withVolume.map((c) => ({
          time: c.time as never,
          value: c.volume as number,
          color:
            c.close >= c.open
              ? "rgba(34,197,94,0.35)"
              : "rgba(244,63,94,0.35)",
        })),
      );
      volumeRef.current = volume;
    }

    for (const line of priceLines) {
      candles.createPriceLine({
        price: line.price,
        color: cssVar(
          line.colour === "pos"
            ? "--c-pos"
            : line.colour === "neg"
              ? "--c-neg"
              : line.colour === "warn"
                ? "--c-warn"
                : "--c-accent",
          "#2dd4bf",
        ),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: line.label,
      });
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: fullscreen ? window.innerHeight - 120 : height,
      });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      seriesRef.current.clear();
      candleRef.current = null;
      volumeRef.current = null;
      chart.remove();
      chartRef.current = null;
    };
    // priceLines is intentionally excluded: re-creating the chart on every
    // parent render would fight the user's pan/zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, height, fullscreen]);

  // Overlays are added/removed without rebuilding the chart.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !data?.indicators) return;

    for (const [key, series] of seriesRef.current.entries()) {
      if (!overlays.includes(key)) {
        chart.removeSeries(series);
        seriesRef.current.delete(key);
      }
    }

    for (const key of overlays) {
      if (seriesRef.current.has(key) || PANE_INDICATORS.has(key)) continue;
      const points = data.indicators[key];
      if (!points?.length) continue;
      const series = chart.addLineSeries({
        color: OVERLAY_COLOURS[key] || "#94a3b8",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: key,
      });
      series.setData(points.map((p) => ({ time: p.time as never, value: p.value })));
      seriesRef.current.set(key, series);
    }
  }, [overlays, data]);

  if (loading) return <Loading label="Loading price history" />;
  if (!data || !data.available) {
    return (
      <Unavailable
        reason={data?.reason || "No history provider returned bars for this instrument."}
        hint={[
          "Yahoo Finance caps intraday history: about 7 days at 1-minute and 60 days at 5–30 minute resolution.",
          "Run the history_refresh job, or configure a licensed provider.",
        ]}
      />
    );
  }

  return (
    <div
      className={clsx(
        fullscreen &&
          "fixed inset-0 z-50 flex flex-col bg-bg p-4",
      )}
    >
      {fullscreen && (
        <div className="mb-2 flex justify-end">
          <button className="btn" onClick={() => setFullscreen(false)}>
            <Minimize2 className="h-3.5 w-3.5" aria-hidden /> Exit fullscreen
          </button>
        </div>
      )}
      <div className="relative">
        <div ref={containerRef} className="w-full" />
        {!fullscreen && (
          <button
            className="btn absolute right-2 top-2 px-2 py-1"
            onClick={() => setFullscreen(true)}
            title="Fullscreen"
          >
            <Maximize2 className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">Fullscreen</span>
          </button>
        )}
      </div>
      {data.provenance && (
        <p className="mt-1.5 text-2xs text-ink-muted">
          {data.candles.length} bars · {data.provenance.source} ·{" "}
          {data.provenance.status}
        </p>
      )}
    </div>
  );
}

export function OverlayPicker({
  available,
  selected,
  onChange,
}: {
  available: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const options = useMemo(
    () =>
      available.filter(
        (key) => OVERLAY_COLOURS[key] !== undefined || PANE_INDICATORS.has(key),
      ),
    [available],
  );

  return (
    <div className="flex flex-wrap gap-1">
      {options.map((key) => {
        const active = selected.includes(key);
        return (
          <button
            key={key}
            type="button"
            aria-pressed={active}
            onClick={() =>
              onChange(
                active ? selected.filter((k) => k !== key) : [...selected, key],
              )
            }
            className={clsx(
              "chip transition-colors",
              active
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-line bg-raised text-ink-muted hover:text-ink",
            )}
          >
            {OVERLAY_COLOURS[key] && (
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: OVERLAY_COLOURS[key] }}
                aria-hidden
              />
            )}
            {key}
          </button>
        );
      })}
    </div>
  );
}
