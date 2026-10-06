import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Performance, PerformanceStats } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { PerformanceView, performancePath, sinceFor } from "./performance";

const STATS: PerformanceStats = {
  signals: 40,
  measured: 36,
  win_rate_pct: 55.6,
  avg_return_pct: 3.2,
  avg_gain_pct: 9.4,
  avg_loss_pct: -4.6,
  expectancy_r: 0.42,
  r_count: 36,
  stop_hit_pct: 30,
  reached_20_pct: 22.5,
  median_days_to_20: 14,
};

const BODY: Performance = {
  horizon: 20,
  since: null,
  first: "2026-01-02",
  last: "2026-10-02",
  total: STATS,
  types: [
    {
      type: "breakout",
      label: "Breakout confirmed",
      r: true,
      all: STATS,
      buckets: [{ bucket: "A", ...STATS, signals: 12, measured: 12 }],
      regimes: [{ regime: "correction", ...STATS, signals: 5, measured: 5, win_rate_pct: 20 }],
    },
    {
      type: "pocket_pivot",
      label: "Pocket pivot",
      r: false,
      all: { ...STATS, expectancy_r: null },
      buckets: [],
      regimes: [],
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("Signal performance", () => {
  it("shows each signal by grade, and by regime on request", async () => {
    mockApi({ "GET /api/performance?horizon=20": { body: BODY } });
    renderWithClient(<PerformanceView />);
    const table = await screen.findByRole("table", { name: /20 sessions after each signal/ });
    const breakout = within(table)
      .getByRole("rowheader", { name: "Breakout confirmed" })
      .closest("tr");
    expect(breakout).toHaveTextContent("40 (36 measured)");
    expect(breakout).toHaveTextContent("56%");
    expect(breakout).toHaveTextContent("+0.42R");
    expect(breakout).toHaveTextContent("▲ +9.4% / ▼ −4.6%");
    expect(within(table).getByRole("rowheader", { name: "Grade A" })).toBeInTheDocument();
    const pivots = within(table).getByRole("rowheader", { name: "Pocket pivot" }).closest("tr");
    expect(pivots).toHaveTextContent("—"); // no R before the entry
    fireEvent.click(within(table).getAllByRole("button", { name: "Show by market regime" })[0]);
    expect(
      within(table).getByRole("rowheader", { name: "Correction" }).closest("tr"),
    ).toHaveTextContent("20%");
  });

  it("asks for the chosen horizon and period", () => {
    expect(performancePath(10, null)).toBe("/api/performance?horizon=10");
    expect(performancePath(20, "2026-04-06")).toBe("/api/performance?horizon=20&since=2026-04-06");
    expect(sinceFor(6, new Date("2026-10-06T12:00:00Z"))).toBe("2026-04-06");
    expect(sinceFor(null)).toBeNull();
  });
});
