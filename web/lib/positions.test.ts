import { describe, expect, it } from "vitest";
import { HOLDING, QUOTE } from "@/components/holdings/fixtures";
import { livePosition, totals } from "@/lib/positions";

describe("live positions", () => {
  it("re-prices with a newer live quote", () => {
    const live = livePosition(HOLDING, QUOTE);
    // (104.20 − 100) × 50 = 210; 4.2%; 4.2 ÷ 8 = 0.53R (0.525); open risk (104.2 − 96) × 50 = 410.
    expect(live).toMatchObject({
      price: 104.2,
      price_source: "live",
      pnl: 210,
      pnl_pct: 4.2,
      r: 0.53,
      position_value: 5210,
      open_risk: 410,
      day_change_pct: -6.96,
    });
  });

  it("keeps closed positions and older quotes as they are", () => {
    expect(livePosition({ ...HOLDING, closed_on: "2026-10-01" }, QUOTE).price).toBe(112);
    const newer = { ...HOLDING, price_source: "live" as const, price_at: "2026-10-02T15:32:00Z" };
    expect(livePosition(newer, QUOTE)).toBe(newer);
    expect(livePosition(HOLDING, undefined)).toBe(HOLDING);
  });

  it("adds up value, P&L and open risk", () => {
    expect(
      totals([HOLDING, { ...HOLDING, pnl: -100, position_value: 1000, open_risk: 0 }]),
    ).toEqual({
      value: 6600,
      pnl: 500,
      openRisk: 800,
      count: 2,
    });
  });
});
