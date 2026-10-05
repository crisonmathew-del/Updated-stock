"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { Kbd } from "@/components/ui/kbd";
import { api, type ChartData } from "@/lib/api";
import { isTyping, plainKey } from "@/lib/keys";
import { cn } from "@/lib/utils";
import { MA_STYLE } from "./price-chart";

const PriceChart = dynamic(() => import("./price-chart"), {
  ssr: false,
  // Fills the box ChartPanel sizes to the chart's height.
  loading: () => <div className="h-full animate-pulse rounded bg-surface-2" />,
});

/** Keys 1-5 (spec §8.1). Intraday arrives with real-time data in Phase 6. */
export const RANGES = [
  { key: "1", label: "6M", timeframe: "daily", sessions: 504, visible: 126 },
  { key: "2", label: "1Y", timeframe: "daily", sessions: 504, visible: 252 },
  { key: "3", label: "2Y", timeframe: "daily", sessions: 504, visible: 504 },
  { key: "4", label: "2Y W", timeframe: "weekly", sessions: 1260, visible: 104 },
  { key: "5", label: "5Y W", timeframe: "weekly", sessions: 1260, visible: 260 },
] as const;

const RANGE_KEY = "breakout:chart-range";
const MA_KEY = "breakout:chart-mas";

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

/** `compact` (the screener's preview) drops the key hints and the legend note. */
export function ChartPanel({
  symbol,
  height = 520,
  compact = false,
}: {
  symbol: string;
  height?: number;
  compact?: boolean;
}) {
  const [rangeKey, setRangeKey] = useState<string>("2");
  const [shown, setShown] = useState<Record<string, boolean>>({});
  useEffect(() => {
    // Remembered choices load after mount (the server can't read localStorage).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRangeKey(stored(RANGE_KEY, "2"));
    setShown(stored(MA_KEY, {}));
  }, []);
  const range = RANGES.find((r) => r.key === rangeKey) ?? RANGES[1];

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTyping(event) || !plainKey(event)) return;
      const match = RANGES.find((r) => r.key === event.key);
      if (match) {
        setRangeKey(match.key);
        localStorage.setItem(RANGE_KEY, JSON.stringify(match.key));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const chart = useQuery({
    queryKey: ["stock", symbol, "chart", range.timeframe, range.sessions],
    queryFn: () =>
      api.get<ChartData>(
        `/api/stocks/${symbol}/chart?timeframe=${range.timeframe}&sessions=${range.sessions}`,
      ),
    staleTime: 5 * 60_000,
  });

  function toggle(key: string) {
    const next = { ...shown, [key]: !(shown[key] ?? true) };
    setShown(next);
    localStorage.setItem(MA_KEY, JSON.stringify(next));
  }

  const mas = chart.data ? Object.keys(chart.data.series.ma) : [];
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
        <div role="group" aria-label="Chart range" className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              aria-pressed={r.key === range.key}
              title={`Press ${r.key}`}
              onClick={() => {
                setRangeKey(r.key);
                localStorage.setItem(RANGE_KEY, JSON.stringify(r.key));
              }}
              className={cn(
                "rounded px-2 py-1",
                r.key === range.key
                  ? "bg-surface-2 text-foreground"
                  : "text-muted hover:text-foreground",
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
        <div role="group" aria-label="Moving averages" className="flex flex-wrap gap-2">
          {mas.map((key) => {
            const style = MA_STYLE[key];
            const on = shown[key] ?? true;
            return (
              <button
                key={key}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(key)}
                className={cn(
                  "flex items-center gap-1 rounded px-1.5 py-0.5",
                  on ? "text-foreground" : "text-muted line-through",
                )}
              >
                <span
                  aria-hidden
                  className="inline-block h-0.5 w-3"
                  style={{ background: `var(${style?.token ?? "--muted"})` }}
                />
                {style?.label ?? key}
              </button>
            );
          })}
        </div>
        {chart.data?.overlay && (
          <span className="text-muted">
            <span aria-hidden className="text-tide">
              ━
            </span>{" "}
            {chart.data.overlay.label} · pivot {chart.data.overlay.pivot.toFixed(2)}
          </span>
        )}
        {!compact && (
          <span className="ml-auto hidden items-center gap-1 text-muted md:flex">
            <Kbd>1</Kbd>–<Kbd>5</Kbd> range
          </span>
        )}
      </div>
      {chart.error && <p className="py-20 text-center text-sm text-fail">{chart.error.message}</p>}
      {chart.isPending && <div style={{ height }} className="animate-pulse rounded bg-surface-2" />}
      {chart.data && (
        <div style={{ height }}>
          <PriceChart
            data={chart.data}
            visible={range.visible}
            shownMas={shown}
            height={height}
            compact={compact}
          />
        </div>
      )}
      {!compact && (
        <p className="text-xs text-muted">
          ▲ pocket pivot · ■ earnings · ● RS high ahead of price · ▼ past signal. Shaded band: buy
          zone. Hollow candles closed up, filled closed down; brighter volume is above the 50-day
          average.
        </p>
      )}
    </div>
  );
}
