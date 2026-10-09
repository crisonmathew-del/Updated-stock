import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { focusRow } from "@/components/shell/shortcuts";
import { LIST } from "@/components/setups/fixtures";
import type { BreadthDay, Groups, Regime, SignalEntry, SignalList } from "@/lib/api";
import { useListStore } from "@/stores/list";
import { useLive } from "@/stores/live";
import { mockApi, renderWithClient } from "@/test-utils";
import { Dashboard } from "./dashboard";
import { divergingLayout, SectorRotation } from "./leadership";
import { latestReturn } from "./lists";
import { MarketPanel, shortDate, stateSince } from "./market-panel";

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useListStore.setState({ source: null, symbols: [] });
  useLive.setState({ quotes: {} });
});

const REGIME: Regime = {
  date: "2026-10-02",
  state: "confirmed_uptrend",
  label: "Confirmed uptrend",
  changed_from: "correction",
  reasons: [
    "SPY 3.9% above its 50-day SMA (833.29 vs 802.00)",
    "Last follow-through day 2026-08-21",
  ],
  indexes: [
    {
      symbol: "SPY",
      state: "confirmed_uptrend",
      label: "Confirmed uptrend",
      close: 833.29,
      ema21: 823.31,
      sma50: 802,
      sma200: 760,
      change_pct: -1.04,
      distribution_days: 2,
      distribution_dates: ["2026-09-22", "2026-10-02"],
      rally_day: null,
      is_ftd: false,
      last_ftd_date: "2026-08-21",
      reasons: [],
    },
  ],
  history: [
    {
      date: "2026-10-02",
      states: { MARKET: "confirmed_uptrend" },
      distribution_days: {},
      is_ftd: false,
    },
    {
      date: "2026-10-01",
      states: { MARKET: "confirmed_uptrend" },
      distribution_days: {},
      is_ftd: false,
    },
    { date: "2026-09-30", states: { MARKET: "correction" }, distribution_days: {}, is_ftd: false },
  ],
};

function day(date: string, pct50: number, net: number): BreadthDay {
  return {
    date,
    members: 5900,
    pct_above_50: pct50,
    pct_above_200: 72.4,
    new_highs: Math.max(net, 0) + 10,
    new_lows: Math.max(-net, 0) + 10,
    net_new_highs: net,
    advancers: 3100,
    decliners: 2700,
    ad_line: 1200,
  };
}

// Newest first, like the API: 58% above the 50-day today vs 62% five sessions earlier.
const BREADTH: BreadthDay[] = [
  day("2026-10-02", 58.2, 45),
  day("2026-10-01", 59, 30),
  day("2026-09-30", 60, -12),
  day("2026-09-29", 61, 8),
  day("2026-09-26", 61.5, 0),
  day("2026-09-25", 62.1, 20),
];

const BREAKOUT: SignalEntry = {
  id: 9,
  date: "2026-10-02",
  type: "breakout",
  type_label: "Breakout confirmed",
  symbol: "SPOT",
  name: "Spotify Technology S.A.",
  setup_id: 5,
  summary: "Breakout confirmed: closed 0.3% above the pivot 92.46 on 330% of average volume.",
  price: 92.75,
  pivot: 92.46,
  entry: 92.56,
  stop: 88.71,
  score: 84.2,
  grade: "A",
  context: {},
  setup_state: "breakout",
  outcome: null,
};

const FAILED: SignalEntry = {
  ...BREAKOUT,
  id: 7,
  date: "2026-09-25",
  symbol: "FAIL",
  name: "Fallow Instruments",
  setup_state: "failed",
  outcome: {
    sessions_observed: 5,
    returns: { "1": 0.4, "5": -2.94, "10": null, "20": null, "60": null },
    returns_r: { "1": 0.1, "5": -0.8, "10": null, "20": null, "60": null },
    mfe_pct: 1.1,
    mae_pct: -4.2,
    stop_hit_on: "2026-09-30",
    target_2r_on: null,
    gain_20_on: null,
    complete: false,
  },
};

const REGIME_CHANGE: SignalEntry = {
  ...BREAKOUT,
  id: 3,
  type: "regime_change",
  type_label: "Market regime change",
  symbol: null,
  name: null,
  summary: "Market regime: Correction → Confirmed uptrend.",
  setup_state: null,
};

const GROUPS: Groups = {
  date: "2026-10-02",
  groups: [
    {
      group_id: 1,
      rank: 1,
      rank_change_4w: 3,
      name: "Semiconductors & Related Devices",
      sector: "Information Technology",
      members: 12,
      median_rs: 88,
      return_3m: 0.184,
      return_6m: 0.31,
      tt_passing: 7,
      new_highs: 4,
    },
  ],
  sectors: [
    {
      symbol: "XLK",
      sector: "Information Technology",
      rank: 1,
      rank_change_4w: 2,
      rs_raw: 1.2,
      return_3m: 0.3,
    },
    {
      symbol: "XLU",
      sector: "Utilities",
      rank: 2,
      rank_change_4w: -1,
      rs_raw: 0.9,
      return_3m: -0.1,
    },
  ],
};

const signals = (items: SignalEntry[]): SignalList => ({ total: items.length, counts: {}, items });

function mockDashboard() {
  return mockApi({
    "GET /api/market/regime?days=60": { body: REGIME },
    "GET /api/market/breadth?days=20": { body: BREADTH },
    "GET /api/setups?state=basing,near_pivot,breakout&sort=score&limit=10": { body: LIST },
    "GET /api/setups?state=near_pivot&sort=readiness&limit=10": { body: LIST },
    "GET /api/signals?type=breakout&limit=10": { body: signals([BREAKOUT, FAILED]) },
    "GET /api/signals?limit=12": { body: signals([BREAKOUT, REGIME_CHANGE]) },
    "GET /api/groups?limit=10": { body: GROUPS },
  });
}

describe("helpers", () => {
  it("formats short dates without shifting the day", () => {
    expect(shortDate("2026-09-01")).toBe("Sep 1");
    expect(shortDate(null)).toBe("—");
  });

  it("finds the first session of the current regime", () => {
    expect(stateSince(REGIME)).toBe("2026-10-01");
  });

  it("returns the longest horizon measured so far", () => {
    expect(latestReturn(FAILED)).toEqual([-2.94, "5d"]);
    expect(latestReturn(BREAKOUT)).toBeNull();
  });

  it("puts zero where the negative and positive ranges meet", () => {
    // −10% … +30%: zero sits a quarter of the way along; a +30% bar fills the rest.
    const layout = divergingLayout([0.3, -0.1, null]);
    expect(layout.zero).toBeCloseTo(25);
    const gain = layout.bar(0.3);
    expect(gain.left).toBeCloseTo(25);
    expect(gain.width).toBeCloseTo(75);
    const loss = layout.bar(-0.1);
    expect(loss.left).toBeCloseTo(0);
    expect(loss.width).toBeCloseTo(25);
    expect(layout.bar(null)).toEqual({ left: 25, width: 0 });
    expect(divergingLayout([0.1, 0.2]).zero).toBe(0);
  });
});

describe("MarketPanel", () => {
  it("leads with the regime, when it started and its reasons, then breadth", async () => {
    mockDashboard();
    renderWithClient(<MarketPanel />);

    expect(await screen.findByText("Confirmed uptrend", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("Since Oct 1")).toBeInTheDocument();
    expect(screen.getByText(/Last follow-through day 2026-08-21/)).toBeInTheDocument();
    const spy = screen.getByText("SPY").closest("tr") as HTMLElement;
    expect(spy).toHaveTextContent("833.29");
    expect(spy).toHaveTextContent("Aug 21");
    expect(within(spy).getByTitle("2026-09-22, 2026-10-02")).toHaveTextContent("2");

    expect(await screen.findByText("58%")).toBeInTheDocument();
    // 58.2 → 58 vs 62.1 → 62: four points lower, with a ▼ so it doesn't rely on colour.
    expect(screen.getAllByText(/in 5 sessions/)[0]).toHaveTextContent("▼ −4 pts in 5 sessions");
    expect(screen.getByText("+45")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Net new highs, last 6 sessions/ })).toBeInTheDocument();
  });

  it("shows the indexes' live prices while the market is open, the close otherwise", async () => {
    const quote = (at: string) => ({
      symbol: "SPY",
      last: 840.12,
      prev_close: 833.29,
      change_pct: 0.82,
      open: 834,
      high: 841,
      low: 833.5,
      volume: 1_000_000,
      partial_volume: true,
      at,
      source: "stream" as const,
    });
    // A quote from the session already processed is just the close.
    useLive.setState({ quotes: { SPY: quote("2026-10-02T19:59:00Z") } });
    mockDashboard();
    renderWithClient(<MarketPanel />);
    const spy = (await screen.findByText("SPY")).closest("tr") as HTMLElement;
    expect(spy).toHaveTextContent("833.29");
    expect(screen.getByRole("columnheader", { name: "Close" })).toBeInTheDocument();

    // The next morning's trading (10:15:30 ET) is live.
    act(() => useLive.setState({ quotes: { SPY: quote("2026-10-05T14:15:30Z") } }));
    expect(spy).toHaveTextContent("840.12");
    expect(spy).toHaveTextContent("Live 10:15:30 ET:");
    expect(within(spy).getByTitle(/close Oct 2 833\.29/)).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Price" })).toBeInTheDocument();
    expect(screen.getByText(/regime and distribution days are judged at the close/)).toBeVisible();
  });

  it("says what to do when nothing is computed yet", async () => {
    mockApi({
      "GET /api/market/regime?days=60": {
        body: {
          ...REGIME,
          date: null,
          state: null,
          label: null,
          reasons: [],
          indexes: [],
          history: [],
        },
      },
      "GET /api/market/breadth?days=20": { body: [] },
    });
    renderWithClient(<MarketPanel />);
    expect(await screen.findByText(/No regime yet/)).toBeInTheDocument();
    expect(await screen.findByText(/No breadth yet/)).toBeInTheDocument();
  });
});

describe("Dashboard", () => {
  it("shows setups, breakouts with their status, groups and signals", async () => {
    mockDashboard();
    renderWithClient(<Dashboard />);

    const top = await screen.findByRole("list", { name: "Top setups by score" });
    const link = within(top).getByRole("link", { name: /SPOT/ });
    expect(link).toHaveAttribute("href", "/stocks/SPOT");
    expect(link).toHaveTextContent("VCP");
    expect(link).toHaveTextContent("Near pivot");
    expect(within(top).getByLabelText("Grade A, score 84")).toBeInTheDocument();

    const near = screen.getByRole("list", { name: "Setups closest to their pivot" });
    expect(near).toHaveTextContent(`1.0% below`);
    expect(near).toHaveTextContent(`pivot 92.46`);

    const breakouts = await screen.findByRole("list", { name: "Recent breakouts" });
    const rows = within(breakouts).getAllByRole("link");
    expect(rows.map((r) => r.getAttribute("href"))).toEqual(["/stocks/SPOT", "/stocks/FAIL"]);
    expect(rows[0]).toHaveTextContent("today");
    expect(rows[0]).toHaveTextContent("Breakout");
    expect(rows[1]).toHaveTextContent("▼ −2.9% 5d");
    expect(rows[1]).toHaveTextContent("Failed");
    expect(screen.getByText("1 on Oct 2")).toBeInTheDocument();

    const feed = await screen.findByRole("list", { name: "Recent signals" });
    const market = within(feed).getByRole("link", { name: /Market regime change/ });
    expect(market).toHaveAttribute("href", "/#market");

    expect(await screen.findByText("Semiconductors & Related Devices")).toBeInTheDocument();
    expect(screen.getAllByLabelText("up 3 places")[0]).toHaveTextContent("▲ 3");
  });

  it("makes the clicked panel the list `[` and `]` flip through", async () => {
    mockDashboard();
    renderWithClient(<Dashboard />);
    const breakouts = await screen.findByRole("list", { name: "Recent breakouts" });
    const link = within(breakouts).getAllByRole("link")[1];
    link.addEventListener("click", (event) => event.preventDefault()); // jsdom can't navigate
    fireEvent.click(link);
    expect(useListStore.getState()).toMatchObject({
      source: "Breakouts",
      symbols: ["SPOT", "FAIL"],
    });
  });

  it("moves focus through rows with j and k", async () => {
    mockDashboard();
    renderWithClient(<Dashboard />);
    const breakouts = await screen.findByRole("list", { name: "Recent breakouts" });
    const [first, second] = within(breakouts).getAllByRole("link");
    first.focus();
    expect(focusRow(1)).toBe(second);
    expect(document.activeElement).toBe(second);
    expect(focusRow(-1)).toBe(first);
  });
});

describe("SectorRotation", () => {
  it("lists sectors by rank with signed returns and rank trends", async () => {
    mockApi({ "GET /api/groups?limit=10": { body: GROUPS } });
    renderWithClient(<SectorRotation />);
    const xlu = (await screen.findByText("Utilities")).closest("tr") as HTMLElement;
    expect(xlu).toHaveTextContent("−10.0%");
    expect(within(xlu).getByLabelText("down 1 places")).toHaveTextContent("▼ 1");
  });
});
