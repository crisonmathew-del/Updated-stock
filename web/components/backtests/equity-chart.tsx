"use client";

import { AreaSeries, createChart, LineSeries, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { BacktestReport } from "@/lib/api";
import { token } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { withAlpha } from "@/components/stock/base-overlay";

/**
 * The strategy's equity against SPY bought with the same capital (one dollar axis), and the
 * drawdown from the running peak underneath. A dashed line marks where out-of-sample starts.
 */
export default function EquityChart({
  equity,
  split,
  height = 380,
}: {
  equity: BacktestReport["equity"];
  split: string | null;
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
        panes: { separatorColor: c.border, enableResize: false },
      },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: { borderColor: c.border },
      crosshair: { mode: 0 },
    });
    const points = (values: (number | null)[]) =>
      equity.dates.flatMap((d, i) =>
        values[i] == null ? [] : [{ time: d as Time, value: values[i] as number }],
      );
    const spy = chart.addSeries(LineSeries, {
      color: c.text,
      lineWidth: 1,
      priceLineVisible: false,
      title: "SPY",
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    spy.setData(points(equity.benchmark));
    const strategy = chart.addSeries(LineSeries, {
      color: c.rise,
      lineWidth: 2,
      priceLineVisible: false,
      title: "Strategy",
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    strategy.setData(points(equity.equity));
    if (split) {
      const at = equity.dates.indexOf(split);
      if (at >= 0) {
        strategy.createPriceLine({
          price: equity.equity[at],
          color: withAlpha(c.tide, 0.6),
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: false,
          title: "Out of sample from here",
        });
      }
    }
    const dd = chart.addSeries(
      AreaSeries,
      {
        lineColor: c.fall,
        topColor: withAlpha(c.fall, 0.05),
        bottomColor: withAlpha(c.fall, 0.35),
        lineWidth: 1,
        priceLineVisible: false,
        title: "Drawdown %",
        priceFormat: { type: "percent", precision: 1, minMove: 0.1 },
      },
      1,
    );
    dd.setData(points(equity.drawdown));
    chart.panes()[1]?.setHeight(Math.round(height * 0.28));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [equity, split, theme, height]);

  return (
    <div
      ref={box}
      style={{ height }}
      role="img"
      aria-label="Equity curve of the strategy against SPY, with the drawdown below"
    />
  );
}
