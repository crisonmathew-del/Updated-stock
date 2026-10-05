import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import type { ChartData } from "@/lib/api";

type Overlay = NonNullable<ChartData["overlay"]>;

export type OverlayColors = {
  tide: string;
  label: string;
  font: string;
};

/** "#21a28e" + 0.12 → "rgba(33, 162, 142, 0.12)". */
export function withAlpha(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? [...value].map((c) => c + c).join("") : value;
  const n = Number.parseInt(full, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * Draws the detected base on the price pane: the buy-zone band (pivot to the top of the buy
 * zone, from the base's start to the right edge), the outline through its swing points and
 * each contraction's number and depth under its low. Pivot, stop and 2R/3R are price lines on
 * the series (they carry axis labels), not drawn here.
 */
export class BaseOverlayPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(
    private readonly overlay: Overlay,
    private readonly colors: OverlayColors,
  ) {
    const renderer: IPrimitivePaneRenderer = { draw: (target) => this.draw(target) };
    this.views = [{ renderer: () => renderer, zOrder: () => "top" }];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
    this.series = param.series as ISeriesApi<"Candlestick">;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  private draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    const o = this.overlay;
    const scale = chart.timeScale();
    const x = (time: string) => scale.timeToCoordinate(time as Time);
    const y = (price: number) => series.priceToCoordinate(price);

    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      // Buy zone: pivot → pivot × (1 + buy zone %), from the base start to the right edge.
      const top = y(o.buy_zone_top);
      const bottom = y(o.pivot);
      const left = x(o.start) ?? 0;
      if (top !== null && bottom !== null) {
        ctx.fillStyle = withAlpha(this.colors.tide, 0.12);
        ctx.fillRect(left, top, Math.max(0, mediaSize.width - left), bottom - top);
      }

      // The outline through the swing points.
      const points: [number, number][] = [];
      for (const p of o.swings) {
        const px = x(p.time);
        const py = y(p.price);
        if (px !== null && py !== null) points.push([px, py]);
      }
      if (points.length >= 2) {
        ctx.strokeStyle = withAlpha(this.colors.tide, 0.75);
        ctx.lineWidth = 1.5;
        ctx.setLineDash([]);
        ctx.beginPath();
        points.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
        ctx.stroke();
      }

      // Numbered contractions with their depth, under each low.
      ctx.font = this.colors.font;
      ctx.fillStyle = this.colors.label;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (const c of o.contractions) {
        const lx = x(c.low.time);
        const ly = y(c.low.price);
        if (lx === null || ly === null) continue;
        ctx.fillText(`${c.number} · −${c.depth_pct.toFixed(1)}%`, lx, ly + 6);
      }
    });
  }
}
