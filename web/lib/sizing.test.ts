import { describe, expect, it } from "vitest";
import { size, type SizingSettings } from "./sizing";

const DEFAULTS: SizingSettings = {
  account_size: 100_000,
  risk_per_trade_pct: 1,
  max_position_pct: 25,
  max_stop_loss_pct: 8,
  profit_take_min_pct: 20,
  account_currency: "USD",
};

describe("size", () => {
  it("matches the server's hand-worked plan (api/tests/test_trade_plan.py)", () => {
    // Entry 92.56, stop 88.71: risk 3.85; 1,000 / 3.85 = 259 shares (the 25% cap allows 270).
    const s = size(92.56, 88.71, DEFAULTS)!;
    expect(s.riskPerShare).toBe(3.85);
    expect(s.shares).toBe(259);
    expect(s.cappedByPosition).toBe(false);
    expect(s.dollarRisk).toBe(997.15);
    expect(s.positionValue).toBe(23973.04);
    expect(s.positionPct).toBe(23.97);
    expect([s.target2r, s.target3r]).toEqual([100.26, 104.11]);
    // First profit level: 92.56 × 1.2 = 111.072 → 111.08; (111.08 − 92.56) / 3.85 = 4.81.
    expect(s.rewardRisk).toBe(4.81);
    expect(s.stopTooWide).toBe(false);
  });

  it("caps the position at 25% of the account", () => {
    // Risk 0.50: the risk budget allows 2,000 shares, the cap 25,000 / 50 = 500.
    const s = size(50, 49.5, DEFAULTS)!;
    expect([s.shares, s.cappedByPosition, s.dollarRisk]).toEqual([500, true, 250]);
  });

  it("flags a stop wider than the maximum loss and rejects impossible numbers", () => {
    expect(size(100, 90, DEFAULTS)!.stopTooWide).toBe(true);
    expect(size(100, 100, DEFAULTS)).toBeNull();
    expect(size(100, 101, DEFAULTS)).toBeNull();
    expect(size(0, -1, DEFAULTS)).toBeNull();
  });
});
