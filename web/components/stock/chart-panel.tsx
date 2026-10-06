"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { Kbd } from "@/components/ui/kbd";
import { api, type ChartData, type IntradayBars, type SetupDetail } from "@/lib/api";
import { isTyping, plainKey } from "@/lib/keys";
import { cn } from "@/lib/utils";
import { MA_STYLE } from "./price-chart";
import { useLiveQuote } from "@/stores/live";
import {
  chartQuery,
  DEFAULT_RANGE,
  INTRADAY,
  intradayQuery,
  RANGES,
  stockQueries,
} from "./queries";

const PriceChart = dynamic(() => import("./price-chart"), {
  ssr: false,
  // Fills the box ChartPanel sizes to the chart's height.
  loading: () => <div className="h-full animate-pulse rounded bg-surface-2" />,
});

const IntradayChart = dynamic(() => import("./intraday-chart"), {
  ssr: false,
  loading: () => <div className="h-full animate-pulse rounded bg-surface-2" />,
});

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

function IntradayView({
  data,
  error,
  height,
  setup,
  quote,
}: {
  data: IntradayBars | undefined;
  error: Error | null;
  height: number;
  setup: SetupDetail | null | undefined;
  quote: ReturnType<typeof useLiveQuote>;
}) {
  if (error) return <p className="py-20 text-center text-sm text-fail">{error.message}</p>;
  if (!data) return <div style={{ height }} className="animate-pulse rounded bg-surface-2" />;
  if (data.time.length === 0) {
    return (
      <p
        style={{ height }}
        className="flex items-center justify-center px-6 text-center text-sm text-muted"
      >
        No minute bars recorded for this stock yet. The streamer records the stocks it watches
        (holdings, setups near their pivot, watchlists) as they trade.
      </p>
    );
  }
  return (
    <>
      <div style={{ height }}>
        <IntradayChart
          data={data}
          quote={quote}
          height={height}
          levels={{ pivot: setup?.pivot, stop: setup?.stop }}
        />
      </div>
      <p className="text-xs text-muted">
        {data.date} in {data.interval}-minute bars, US/Eastern; bars before 09:30 are pre-market.
        Dotted: previous close; solid teal: pivot; dashed: stop.
      </p>
    </>
  );
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
  const [rangeKey, setRangeKey] = useState<string>(DEFAULT_RANGE.key);
  const [shown, setShown] = useState<Record<string, boolean>>({});
  useEffect(() => {
    // Remembered choices load after mount (the server can't read localStorage).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRangeKey(stored(RANGE_KEY, DEFAULT_RANGE.key));
    setShown(stored(MA_KEY, {}));
  }, []);
  const intraday = compact ? undefined : INTRADAY.find((r) => r.key === rangeKey);
  const range = RANGES.find((r) => r.key === rangeKey) ?? DEFAULT_RANGE;

  // Fetch the chart's code while its data loads, rather than after (the chart is the hero).
  useEffect(() => {
    void import("./price-chart");
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTyping(event) || !plainKey(event)) return;
      const match = [...RANGES, ...(compact ? [] : INTRADAY)].find((r) => r.key === event.key);
      if (match) {
        setRangeKey(match.key);
        localStorage.setItem(RANGE_KEY, JSON.stringify(match.key));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [compact]);

  const chart = useQuery({
    queryKey: chartQuery(symbol, range.timeframe, range.sessions).key,
    queryFn: () => api.get<ChartData>(chartQuery(symbol, range.timeframe, range.sessions).path),
    staleTime: 5 * 60_000,
    enabled: !intraday,
  });
  const minutes = useQuery({
    queryKey: intradayQuery(symbol, intraday?.interval ?? 1).key,
    queryFn: () => api.get<IntradayBars>(intradayQuery(symbol, intraday?.interval ?? 1).path),
    enabled: Boolean(intraday),
    // Minute bars are stored as the session trades; live quotes fill in between.
    refetchInterval: 60_000,
  });
  const setup = useQuery({
    queryKey: stockQueries(symbol).setup.key,
    queryFn: () => api.get<SetupDetail | null>(stockQueries(symbol).setup.path),
    enabled: Boolean(intraday),
  });
  const quote = useLiveQuote(symbol);

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
          {[...RANGES, ...(compact ? [] : INTRADAY)].map((r) => (
            <button
              key={r.key}
              type="button"
              aria-pressed={r.key === (intraday?.key ?? range.key)}
              title={`Press ${r.key}`}
              onClick={() => {
                setRangeKey(r.key);
                localStorage.setItem(RANGE_KEY, JSON.stringify(r.key));
              }}
              className={cn(
                "rounded px-2 py-1 whitespace-nowrap",
                r.key === (intraday?.key ?? range.key)
                  ? "bg-surface-2 text-foreground"
                  : "text-muted hover:text-foreground",
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
        {!intraday && (
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
        )}
        {!intraday && chart.data?.overlay && (
          <span className="text-muted">
            <span aria-hidden className="text-tide">
              ━
            </span>{" "}
            {chart.data.overlay.label} · pivot {chart.data.overlay.pivot.toFixed(2)}
          </span>
        )}
        {!compact && (
          <span className="ml-auto hidden items-center gap-1 text-muted md:flex">
            <Kbd>1</Kbd>–<Kbd>7</Kbd> range
          </span>
        )}
      </div>
      {intraday && (
        <IntradayView
          data={minutes.data}
          error={minutes.error}
          height={height}
          setup={setup.data}
          quote={quote}
        />
      )}
      {!intraday && chart.error && (
        <p className="py-20 text-center text-sm text-fail">{chart.error.message}</p>
      )}
      {!intraday && chart.isPending && (
        <div style={{ height }} className="animate-pulse rounded bg-surface-2" />
      )}
      {!intraday && chart.data && (
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
      {!compact && !intraday && (
        <p className="text-xs text-muted">
          ▲ pocket pivot · ■ earnings · ● RS high ahead of price · ▼ past signal. Shaded band: buy
          zone. Hollow candles closed up, filled closed down; brighter volume is above the 50-day
          average.
        </p>
      )}
    </div>
  );
}
