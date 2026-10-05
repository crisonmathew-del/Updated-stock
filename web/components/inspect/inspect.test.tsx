import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Fundamentals, Groups, Regime, StockSummary } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { GroupsPanel } from "./groups-panel";
import { RegimePanel } from "./regime-panel";
import { StockPanel } from "./stock-panel";

afterEach(() => {
  vi.unstubAllGlobals();
});

const NVDA: StockSummary = {
  symbol: "NVDA",
  name: "NVIDIA Corporation",
  exchange: "NASDAQ",
  type: "common",
  sector: "Information Technology",
  industry: "Semiconductors & Related Devices",
  market_cap: null,
  date: "2026-10-02",
  close: 181.5,
  prev_close: 179.2,
  change: 2.3,
  change_pct: 1.28,
  volume: 41_000_000,
  volume_ratio: 1.1,
  high_52w: 190.1,
  low_52w: 102.4,
  next_earnings: null,
  sessions_to_earnings: null,
  fundamentals_grade: null,
  stage: 2,
  stage_label: "Stage 2 · advancing",
  rs_rating: 94,
  trend_template_passed: 7,
  trend_template_pass: false,
  checks: [
    {
      key: "above_50",
      label: "Price above the 50-day average",
      passed: true,
      detail: "Close 181.50 vs 50-day 170.20",
    },
    {
      key: "near_52w_high",
      label: "Price close enough to the 52-week high",
      passed: false,
      detail: "-27.0% from the 52-week high 248.60 (needs within 25%)",
    },
  ],
  group: {
    id: 3,
    name: "Semiconductors & Related Devices",
    sector: "Information Technology",
    rank: 4,
    rank_change_4w: 6,
    ranked_groups: 152,
  },
  indicators: { sma50: 170.2, roc_63: 0.184, rs_new_high_ahead: true, avg_volume_50: 212345678 },
};

const NO_FUNDAMENTALS: Fundamentals = {
  symbol: "NVDA",
  as_of: "2026-10-02",
  refreshed_at: null,
  grade: null,
  quarters: [],
  years: [],
  earnings: [],
  insiders: [],
};

describe("StockPanel", () => {
  it("shows the checklist with pass/fail marks and numbers", async () => {
    mockApi({
      "GET /api/stocks/NVDA": { body: NVDA },
      "GET /api/stocks/NVDA/fundamentals": { body: NO_FUNDAMENTALS },
      "GET /api/stocks/NVDA/patterns": { body: [] },
    });
    renderWithClient(<StockPanel />);

    fireEvent.change(screen.getByLabelText("Ticker"), { target: { value: "nvda" } });
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));

    expect(await screen.findByText("Stage 2 · advancing")).toBeInTheDocument();
    expect(screen.getByText("7/8")).toBeInTheDocument();
    expect(screen.getByText("4 of 152")).toBeInTheDocument();
    expect(screen.getByText(/▲ 6 in 4 weeks/)).toBeInTheDocument();
    const failing = screen.getByText("Price close enough to the 52-week high").closest("li");
    expect(failing).toHaveTextContent("Fail:");
    expect(failing).toHaveTextContent("-27.0% from the 52-week high 248.60 (needs within 25%)");
    expect(screen.getByText("+18.4%")).toBeInTheDocument();
    expect(screen.getByText("212,345,678")).toBeInTheDocument();
    expect(await screen.findByText(/Statements for NVDA aren.t loaded yet/)).toBeInTheDocument();
    expect(
      await screen.findByText(/No bases or entry events detected for NVDA/),
    ).toBeInTheDocument();
  });

  it("explains an unknown ticker", async () => {
    mockApi({
      "GET /api/stocks/NOPE": { status: 404, body: { detail: "No ticker NOPE in the universe." } },
    });
    renderWithClient(<StockPanel initialSymbol="NOPE" />);
    expect(await screen.findByText("No ticker NOPE in the universe.")).toBeInTheDocument();
  });
});

const REGIME: Regime = {
  date: "2026-10-02",
  state: "uptrend_under_pressure",
  label: "Uptrend under pressure",
  changed_from: "confirmed_uptrend",
  reasons: [
    "QQQ: uptrend under pressure",
    "5 distribution days in the last 25 sessions (Sep 3, Sep 9)",
  ],
  indexes: [
    {
      symbol: "QQQ",
      state: "uptrend_under_pressure",
      label: "Uptrend under pressure",
      close: 590.1,
      ema21: 585,
      sma50: 570,
      sma200: 520,
      change_pct: -0.4,
      distribution_days: 5,
      distribution_dates: [],
      rally_day: null,
      is_ftd: false,
      last_ftd_date: "2026-04-22",
      reasons: [],
    },
  ],
  history: [
    {
      date: "2026-10-02",
      states: { MARKET: "uptrend_under_pressure" },
      distribution_days: { QQQ: 5 },
      is_ftd: false,
    },
  ],
};

describe("RegimePanel", () => {
  it("leads with the state and its reasons", async () => {
    mockApi({ "GET /api/market/regime?days=30": { body: REGIME } });
    renderWithClient(<RegimePanel />);

    expect(await screen.findByText("Uptrend under pressure")).toBeInTheDocument();
    expect(screen.getByText(/5 distribution days in the last 25 sessions/)).toBeInTheDocument();
    const row = screen.getByText("QQQ", { selector: "td" }).closest("tr");
    expect(row).toHaveTextContent("590.10");
    expect(row).toHaveTextContent("2026-04-22");
  });

  it("says when there is no regime yet", async () => {
    mockApi({
      "GET /api/market/regime?days=30": {
        body: { ...REGIME, state: null, label: null, reasons: [], indexes: [], history: [] },
      },
    });
    renderWithClient(<RegimePanel />);
    expect(await screen.findByText(/No regime yet/)).toBeInTheDocument();
  });
});

describe("GroupsPanel", () => {
  it("lists groups and sectors with rank changes as symbols", async () => {
    const groups: Groups = {
      date: "2026-10-02",
      groups: [
        {
          group_id: 1,
          rank: 1,
          rank_change_4w: -2,
          name: "Semiconductors",
          sector: "Information Technology",
          members: 31,
          median_rs: 88,
          return_3m: 0.21,
          return_6m: 0.4,
          tt_passing: 12,
          new_highs: 6,
        },
      ],
      sectors: [
        {
          symbol: "XLK",
          sector: "Information Technology",
          rank: 1,
          rank_change_4w: 0,
          rs_raw: 0.2,
          return_3m: 0.12,
        },
      ],
    };
    mockApi({ "GET /api/groups?limit=20": { body: groups } });
    renderWithClient(<GroupsPanel />);

    const row = (await screen.findByText("Semiconductors")).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("▼ 2")).toBeInTheDocument();
    expect(row).toHaveTextContent("+21.0%");
    expect(screen.getByText("XLK")).toBeInTheDocument();
    expect(screen.getByText("– 0")).toBeInTheDocument();
  });
});
