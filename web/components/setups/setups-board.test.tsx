import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SetupDetail, SetupList, SetupRow } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { SetupsBoard } from "./setups-board";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ROW: SetupRow = {
  id: 5,
  symbol: "SPOT",
  name: "Spotify Technology S.A.",
  kind: "base",
  pattern_type: "vcp",
  pattern_label: "Volatility contraction (VCP)",
  state: "near_pivot",
  state_label: "Near pivot",
  state_since: "2025-04-29",
  first_seen: "2025-04-29",
  as_of: "2025-04-30",
  active: true,
  close: 91.5,
  pivot: 92.46,
  base_low: 75.62,
  readiness_pct: 1.04,
  score: 83.5,
  raw_score: 83.5,
  grade: "A",
  best_grade: "A",
  breakout_date: null,
  entry: 92.56,
  stop: 88.71,
  shares: 259,
  risk_too_wide: false,
  red_flags: ["Earnings risk"],
  closed_on: null,
  closed_reason: null,
};

const LIST: SetupList = {
  as_of: "2025-04-30",
  total: 1,
  counts: { near_pivot: 1, watch: 3 },
  items: [ROW],
};

const DETAIL: SetupDetail = {
  ...ROW,
  regime_multiplier: 1,
  penalties: 0,
  components: [
    {
      key: "trend",
      label: "Trend Template & stage",
      points: 20,
      max_points: 20,
      status: "pass",
      detail: "8/8 Trend Template checks pass; Stage 2.",
    },
    {
      key: "fundamentals",
      label: "Fundamentals Grade",
      points: 0,
      max_points: 20,
      status: "no_data",
      detail: "Fundamentals unknown (no grade): left out, the other parts scaled up.",
    },
  ],
  red_flag_details: [
    {
      key: "earnings_soon",
      label: "Earnings risk",
      penalty: 0,
      detail: "Results expected 2025-05-05, 3 session(s) away: a gap either way is possible.",
    },
  ],
  trade_plan: {
    entry: 92.56,
    stop: 88.71,
    stop_basis: "logical",
    logical_stop: 88.71,
    max_loss_stop: 85.15,
    risk_too_wide: false,
    risk_per_share: 3.85,
    risk_pct: 4.16,
    shares: 259,
    capped_by_position_limit: false,
    dollar_risk: 997.15,
    position_value: 23973.04,
    position_pct: 23.97,
    buy_zone: [92.46, 97.08],
    target_2r: 100.26,
    target_3r: 104.11,
    profit_take: [111.08, 115.7],
    breakeven_at: 100.26,
    breakeven_basis: "2R",
    trail_aggressive: 90.1,
    trail_standard: 88.2,
    reward_risk: 4.81,
    currency: "USD",
    notes: [],
  },
  transitions: [
    {
      date: "2025-04-29",
      from_state: null,
      to_state: "near_pivot",
      to_label: "Near pivot",
      reason: "New volatility contraction (VCP) setup (pivot 92.46). Close 1.0% below the pivot.",
    },
  ],
  signals: [],
  pattern: null,
};

describe("SetupsBoard", () => {
  it("lists setups with their plan and opens the breakdown", async () => {
    mockApi({
      "GET /api/setups?sort=score&limit=200": { body: LIST },
      "GET /api/setups/5": { body: DETAIL },
    });
    renderWithClient(<SetupsBoard />);

    const row = (await screen.findByRole("button", { name: /SPOT/ })).closest("tr")!;
    expect(row).toHaveTextContent("Volatility contraction (VCP)");
    expect(row).toHaveTextContent("Near pivot");
    expect(row).toHaveTextContent("1.0% below");
    expect(row).toHaveTextContent("92.56 / 88.71");
    expect(row).toHaveTextContent("⚠ Earnings risk");
    expect(screen.getByRole("button", { name: "Watch 3" })).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: /SPOT/ }));
    const detail = await screen.findByLabelText("SPOT setup details");
    expect(within(detail).getByText("Trend Template & stage")).toBeInTheDocument();
    expect(within(detail).getByLabelText("no data, left out")).toBeInTheDocument();
    expect(detail).toHaveTextContent("259 (23.97% of the account)");
    expect(detail).toHaveTextContent("100.26 / 104.11");
    expect(detail).toHaveTextContent("New volatility contraction (VCP) setup");
  });

  it("filters by stage", async () => {
    const fetchMock = mockApi({
      "GET /api/setups?sort=score&limit=200": { body: LIST },
      "GET /api/setups?sort=score&limit=200&state=breakout": {
        body: { ...LIST, total: 0, items: [] },
      },
    });
    renderWithClient(<SetupsBoard />);
    fireEvent.click(await screen.findByRole("button", { name: /^Breakout/ }));
    expect(await screen.findByText("No setups match.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/setups?sort=score&limit=200&state=breakout",
      expect.anything(),
    );
  });
});
