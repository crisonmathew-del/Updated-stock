"use client";

import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarkerBar,
  type Time,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChartData } from "@/lib/api";
import { formatCompact, formatPrice } from "@/lib/format";
import { token } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { BaseOverlayPrimitive, withAlpha } from "./base-overlay";

export const MA_STYLE: Record<string, { label: string; token: string; width: 1 | 2 }> = {
  ema10: { label: "10 EMA", token: "--ma-10", width: 1 },
  ema21: { label: "21 EMA", token: "--ma-21", width: 1 },
  sma50: { label: "50", token: "--ma-50", width: 1 },
  sma150: { label: "150", token: "--ma-150", width: 1 },
  sma200: { label: "200", token: "--ma-200", width: 2 },
  sma10w: { label: "10W", token: "--ma-50", width: 1 },
  sma30w: { label: "30W", token: "--ma-150", width: 1 },
  sma40w: { label: "40W", token: "--ma-200", width: 2 },
};

type Legend = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  change: number | null;
  volume: number;
  avgVolume: number | null;
  ma: [string, number | null][];
  notes: string[];
};

function legendAt(data: ChartData, index: number): Legend {
  const s = data.series;
  const previous = index > 0 ? s.close[index - 1] : null;
  const time = s.time[index];
  return {
    time,
    open: s.open[index],
    high: s.high[index],
    low: s.low[index],
    close: s.close[index],
    change: previous ? (s.close[index] / previous - 1) * 100 : null,
    volume: s.volume[index],
    avgVolume: s.avg_volume[index],
    ma: Object.entries(s.ma).map(([k, values]) => [k, values[index]]),
    notes: data.markers.filter((m) => m.time === time).map((m) => m.text),
  };
}

/**
 * The stock page's chart: candles (up hollow, down filled), volume with above-average sessions
 * stronger, moving averages, the RS line in its own pane, markers, and the base with its
 * pivot, buy zone, stop and 2R/3R. `visible` is how many bars to show at first.
 */
export function PriceChart({
  data,
  visible,
  shownMas,
  height = 520,
  compact = false,
}: {
  data: ChartData;
  visible: number;
  shownMas: Record<string, boolean>;
  height?: number;
  /** A small chart (the screener preview): no axis labels for the averages and 2R/3R. */
  compact?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const maSeries = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const theme = useTheme();
  const last = useMemo(() => legendAt(data, data.series.time.length - 1), [data]);
  const [legend, setLegend] = useState<Legend>(last);

  useEffect(() => {
    const el = box.current;
    if (!el || data.series.time.length === 0) return;
    const c = {
      rise: token("--rise"),
      fall: token("--fall"),
      tide: token("--tide"),
      warn: token("--warn"),
      text: token("--muted"),
      fg: token("--foreground"),
      grid: token("--chart-grid"),
      border: token("--border"),
    };
    const font = getComputedStyle(document.body).fontFamily;
    const chart = createChart(el, {
      autoSize: true,
      // Pin the locale: some browsers report tags Intl rejects (e.g. "en-US@posix").
      localization: { locale: "en-US" },
      layout: {
        background: { color: "transparent" },
        textColor: c.text,
        fontFamily: font,
        fontSize: 11,
        attributionLogo: false,
        panes: { separatorColor: c.border, enableResize: false },
      },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: { borderColor: c.border, rightOffset: 4, minBarSpacing: 1.5 },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    const s = data.series;

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
      s.time.map((t, i) => ({
        time: t as Time,
        open: s.open[i],
        high: s.high[i],
        low: s.low[i],
        close: s.close[i],
      })),
    );

    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(
      s.time.map((t, i) => {
        const up = s.close[i] >= s.open[i];
        const heavy = s.avg_volume[i] != null && s.volume[i] > (s.avg_volume[i] as number);
        return {
          time: t as Time,
          value: s.volume[i],
          color: withAlpha(up ? c.rise : c.fall, heavy ? 0.8 : 0.3),
        };
      }),
    );

    maSeries.current = new Map();
    for (const [key, values] of Object.entries(s.ma)) {
      const style = MA_STYLE[key];
      const line = chart.addSeries(LineSeries, {
        color: token(style?.token ?? "--muted"),
        lineWidth: style?.width ?? 1,
        priceLineVisible: false,
        lastValueVisible: !compact,
        title: compact ? "" : (style?.label ?? key),
        crosshairMarkerVisible: false,
        visible: shownMas[key] ?? true,
      });
      line.setData(
        s.time.flatMap((t, i) =>
          values[i] == null ? [] : [{ time: t as Time, value: values[i] as number }],
        ),
      );
      maSeries.current.set(key, line);
    }

    const rs = chart.addSeries(
      LineSeries,
      { color: c.fg, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "RS" },
      1,
    );
    rs.setData(
      s.time.flatMap((t, i) =>
        s.rs_line[i] == null ? [] : [{ time: t as Time, value: s.rs_line[i] as number }],
      ),
    );
    rs.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });
    chart.panes()[1]?.setHeight(Math.round(height * 0.17));

    const MARKER: Record<string, Pick<SeriesMarkerBar<Time>, "position" | "shape" | "color">> = {
      pocket_pivot: { position: "belowBar", shape: "arrowUp", color: c.tide },
      gap: { position: "belowBar", shape: "circle", color: c.tide },
      earnings: { position: "belowBar", shape: "square", color: c.text },
      rs_high: { position: "aboveBar", shape: "circle", color: c.rise },
      signal: { position: "aboveBar", shape: "arrowDown", color: c.fg },
    };
    createSeriesMarkers(
      candles,
      data.markers.map((m) => ({
        time: m.time as Time,
        // A small chart keeps the shapes; the labels would crowd it.
        text: compact ? undefined : m.label,
        size: 0.8,
        ...MARKER[m.kind],
      })),
    );

    const o = data.overlay;
    if (o) {
      candles.attachPrimitive(
        new BaseOverlayPrimitive(o, { tide: c.tide, label: c.text, font: `11px ${font}` }),
      );
      const line = (
        price: number | null,
        color: string,
        title: string,
        style: LineStyle,
        label = true,
      ) => {
        if (price == null) return;
        candles.createPriceLine({
          price,
          color,
          title,
          lineStyle: style,
          lineWidth: 1,
          axisLabelVisible: label,
        });
      };
      line(o.pivot, c.tide, "Pivot", LineStyle.Solid);
      line(o.stop, c.fall, "Stop", LineStyle.Dashed);
      line(o.target_2r, c.rise, "2R", LineStyle.Dotted, !compact);
      line(o.target_3r, c.rise, "3R", LineStyle.Dotted, !compact);
    }

    const count = s.time.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, count - visible), to: count + 3 });

    const index = new Map(s.time.map((t, i) => [t, i]));
    const onMove = (param: MouseEventParams<Time>) => {
      const i = param.time !== undefined ? index.get(String(param.time)) : undefined;
      setLegend(i === undefined ? legendAt(data, count - 1) : legendAt(data, i));
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
    };
    // shownMas is applied by the effect below without rebuilding the chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, theme, visible, height, compact]);

  useEffect(() => {
    for (const [key, series] of maSeries.current) {
      series.applyOptions({ visible: shownMas[key] ?? true });
    }
  }, [shownMas]);

  const l = legend;
  return (
    <div className="relative">
      <div
        className="pointer-events-none absolute top-1 left-2 z-10 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs"
        aria-live="off"
      >
        <span className="text-muted">{l.time}</span>
        <span>
          O <span className="tabular">{formatPrice(l.open)}</span> H{" "}
          <span className="tabular">{formatPrice(l.high)}</span> L{" "}
          <span className="tabular">{formatPrice(l.low)}</span> C{" "}
          <span className="tabular">{formatPrice(l.close)}</span>
        </span>
        {l.change != null && (
          <span className={l.change >= 0 ? "text-rise" : "text-fall"}>
            {l.change >= 0 ? "▲ +" : "▼ −"}
            {Math.abs(l.change).toFixed(2)}%
          </span>
        )}
        <span className="text-muted">
          Vol {formatCompact(l.volume)}
          {l.avgVolume ? ` (${(l.volume / l.avgVolume).toFixed(1)}× avg)` : ""}
        </span>
        {l.notes.length > 0 && <span className="text-foreground">{l.notes.join(" · ")}</span>}
      </div>
      <div ref={box} style={{ height }} className="w-full" />
    </div>
  );
}

export default PriceChart;
