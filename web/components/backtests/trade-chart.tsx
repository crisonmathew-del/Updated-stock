"use client";

import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { TradeChart as TradeChartData } from "@/lib/api";
import { token } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";

/** One trade on its daily chart: the entry (▲), each sale (▼ with the reason), the stop at entry
 * and the 50-day average the trailing exit watches. */
export default function TradeChart({
  data,
  height = 340,
}: {
  data: TradeChartData;
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const c = {
      rise: token("--rise"),
      fall: token("--fall"),
      tide: token("--tide"),
      text: token("--muted"),
      ma50: token("--ma-50"),
      grid: token("--chart-grid"),
      border: token("--border"),
    };
    const chart = createChart(el, {
      autoSize: true,
      localization: { locale: "en-US" },
      layout: {
        background: { color: "transparent" },
        textColor: c.text,
        fontFamily: getComputedStyle(document.body).fontFamily,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: { borderColor: c.border, rightOffset: 3 },
      crosshair: { mode: 0 },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "rgba(0,0,0,0)",
      borderUpColor: c.rise,
      wickUpColor: c.rise,
      downColor: c.fall,
      borderDownColor: c.fall,
      wickDownColor: c.fall,
      priceLineVisible: false,
    });
    candles.setData(
      data.time.map((t, i) => ({
        time: t as Time,
        open: data.open[i],
        high: data.high[i],
        low: data.low[i],
        close: data.close[i],
      })),
    );
    const sma = chart.addSeries(LineSeries, {
      color: c.ma50,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "50-day",
      crosshairMarkerVisible: false,
    });
    sma.setData(
      data.time.flatMap((t, i) =>
        data.sma50[i] == null ? [] : [{ time: t as Time, value: data.sma50[i] as number }],
      ),
    );
    const t = data.trade;
    candles.createPriceLine({
      price: t.stop,
      color: c.fall,
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      title: "Stop",
      axisLabelVisible: true,
    });
    candles.createPriceLine({
      price: t.entry_price,
      color: c.tide,
      lineStyle: LineStyle.Solid,
      lineWidth: 1,
      title: "Entry",
      axisLabelVisible: true,
    });
    createSeriesMarkers(candles, [
      {
        time: t.entry_date as Time,
        position: "belowBar",
        shape: "arrowUp",
        color: c.tide,
        text: `Buy ${t.shares}`,
      },
      ...t.exits.map((f) => ({
        time: f.date as Time,
        position: "aboveBar" as const,
        shape: "arrowDown" as const,
        color: c.text,
        text: `Sell ${f.shares}`,
      })),
    ]);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, theme]);

  return (
    <div
      ref={box}
      style={{ height }}
      role="img"
      aria-label={`${data.symbol} daily chart with the trade's entry, exits and stop`}
    />
  );
}
