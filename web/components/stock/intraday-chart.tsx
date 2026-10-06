"use client";

import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { IntradayBars, LiveQuote } from "@/lib/api";
import { token } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { withAlpha } from "./base-overlay";

const ET = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** Chart times are UTC seconds; axis and crosshair read US/Eastern clock time. */
export function etClock(time: number): string {
  return ET.format(new Date(time * 1000));
}

/** Where a live print lands: the start of its 1- or 5-minute bar, in UTC seconds. */
export function bucketOf(iso: string, interval: number): number {
  const seconds = Math.floor(new Date(iso).getTime() / 1000);
  return seconds - (seconds % (interval * 60));
}

export type Levels = { pivot?: number | null; stop?: number | null; entry?: number | null };

/**
 * A session in 1- or 5-minute candles (pre-market before the 09:30 line), volume beneath, the
 * previous close, and the setup's pivot and stop. Live quotes move the last bar (or start the
 * next one) between refetches; volume comes only from the stored bars.
 */
export default function IntradayChart({
  data,
  quote,
  levels,
  height = 520,
}: {
  data: IntradayBars;
  quote?: LiveQuote;
  levels: Levels;
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lastBar = useRef<{
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
  } | null>(null);
  const theme = useTheme();

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const c = {
      rise: token("--rise"),
      fall: token("--fall"),
      tide: token("--tide"),
      text: token("--muted"),
      grid: token("--chart-grid"),
      border: token("--border"),
    };
    const chart = createChart(el, {
      autoSize: true,
      localization: { locale: "en-US", timeFormatter: (t: Time) => `${etClock(t as number)} ET` },
      layout: {
        background: { color: "transparent" },
        textColor: c.text,
        fontFamily: getComputedStyle(document.body).fontFamily,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: {
        borderColor: c.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: (t: Time) => etClock(t as number),
      },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "rgba(0,0,0,0)",
      borderUpColor: c.rise,
      wickUpColor: c.rise,
      downColor: c.fall,
      borderDownColor: c.fall,
      wickDownColor: c.fall,
      priceLineVisible: true,
    });
    candlesRef.current = candles;
    const bars = data.time.map((t, i) => ({
      time: t as UTCTimestamp,
      open: data.open[i],
      high: data.high[i],
      low: data.low[i],
      close: data.close[i],
    }));
    candles.setData(bars);
    const last = bars.at(-1);
    lastBar.current = last ? { ...last, time: last.time as number } : null;

    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(
      data.time.map((t, i) => ({
        time: t as UTCTimestamp,
        value: data.volume[i],
        color: withAlpha(data.close[i] >= data.open[i] ? c.rise : c.fall, 0.45),
      })),
    );

    const line = (
      price: number | null | undefined,
      color: string,
      title: string,
      style: LineStyle,
    ) => {
      if (price == null) return;
      candles.createPriceLine({
        price,
        color,
        title,
        lineStyle: style,
        lineWidth: 1,
        axisLabelVisible: true,
      });
    };
    line(data.prev_close, c.text, "Prev close", LineStyle.Dotted);
    line(levels.pivot, c.tide, "Pivot", LineStyle.Solid);
    line(levels.stop, c.fall, "Stop", LineStyle.Dashed);
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
      candlesRef.current = null;
    };
  }, [data, theme, levels.pivot, levels.stop]);

  // A live quote from this session updates the forming bar, or starts the next one.
  useEffect(() => {
    const candles = candlesRef.current;
    const bar = lastBar.current;
    if (!candles || !quote?.at || !data.date) return;
    const day = new Date(quote.at).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    if (day !== data.date) return;
    const time = bucketOf(quote.at, data.interval);
    if (bar && time < bar.time) return;
    const next =
      bar && time === bar.time
        ? {
            ...bar,
            high: Math.max(bar.high, quote.last),
            low: Math.min(bar.low, quote.last),
            close: quote.last,
          }
        : { time, open: quote.last, high: quote.last, low: quote.last, close: quote.last };
    lastBar.current = next;
    candles.update({ ...next, time: next.time as UTCTimestamp });
  }, [quote, data.date, data.interval]);

  return (
    <div
      ref={box}
      style={{ height }}
      role="img"
      aria-label={`${data.symbol} intraday chart, ${data.interval}-minute bars on ${data.date}`}
    />
  );
}
