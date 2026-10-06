import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bucketOf, etClock } from "@/components/stock/intraday-chart";
import type { SetupRow } from "@/lib/api";
import { useLive } from "@/stores/live";
import { mockApi, renderWithClient } from "@/test-utils";
import { boardRows, LiveBoard } from "./live-board";

function setup(id: number, symbol: string, close: number, pivot: number): SetupRow {
  return {
    id,
    symbol,
    name: symbol,
    kind: "base",
    pattern_type: "vcp",
    pattern_label: "Volatility contraction (VCP)",
    state: "near_pivot",
    state_label: "Near pivot",
    state_since: "2026-10-01",
    first_seen: "2026-09-01",
    as_of: "2026-10-01",
    active: true,
    close,
    pivot,
    base_low: null,
    readiness_pct: null,
    score: 84,
    raw_score: 84,
    grade: "A",
    best_grade: "A",
    breakout_date: null,
    entry: null,
    stop: null,
    shares: null,
    risk_too_wide: null,
    red_flags: [],
    closed_on: null,
    closed_reason: null,
  };
}

const SPOT = setup(1, "SPOT", 91.5, 92.46);
const AAPL = setup(2, "AAPL", 248, 250);
const QUOTE = {
  symbol: "SPOT",
  last: 92.65,
  prev_close: 91.5,
  change_pct: 1.26,
  open: 91.6,
  high: 92.65,
  low: 91.5,
  volume: 465_000,
  partial_volume: false,
  at: "2026-10-02T14:15:30Z",
};
const EVENT = {
  kind: "breakout_provisional" as const,
  symbol: "SPOT",
  setup_id: 1,
  price: 92.65,
  at: "2026-10-02T14:15:30Z",
  title: "SPOT breaking out strongly (provisional)",
};

beforeEach(() => {
  useLive.setState({
    connected: true,
    quotes: {},
    events: [],
    scans: { premarket: null, sweep: null },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("live board", () => {
  it("puts today's events first, then the closest to the pivot", () => {
    // AAPL 248 vs 250 = −0.8%; SPOT 91.50 vs 92.46 = −1.04%: AAPL first without events.
    expect(boardRows([SPOT, AAPL], {}, []).map((r) => r.setup.symbol)).toEqual(["AAPL", "SPOT"]);
    const rows = boardRows([SPOT, AAPL], { SPOT: QUOTE }, [EVENT]);
    expect(rows.map((r) => r.setup.symbol)).toEqual(["SPOT", "AAPL"]);
    // 92.65 ÷ 92.46 − 1 = +0.21%.
    expect(rows[0].fromPivot).toBeCloseTo(0.2055, 3);
  });

  it("shows live prices, events and scans", async () => {
    mockApi({
      "GET /api/setups?state=near_pivot,breakout,basing&limit=200": {
        body: { as_of: "2026-10-01", total: 2, counts: {}, items: [SPOT, AAPL] },
      },
      "GET /api/alerts/status": {
        body: {
          email: { configured: true, provider: "mailpit", detail: "" },
          streamer: {
            alive: true,
            state: "streaming",
            provider: "replay",
            detail: null,
            since: null,
          },
          quiet_hours_now: false,
        },
      },
    });
    useLive.setState({
      quotes: { SPOT: QUOTE },
      events: [EVENT],
      scans: {
        premarket: {
          at: "2026-10-02T12:45:00Z",
          items: [
            {
              scan: "premarket",
              symbol: "NVDA",
              name: "NVIDIA",
              at: "2026-10-02T12:45:00Z",
              price: 105,
              prev_close: 100,
              change_pct: 5,
              volume: 40000,
              volume_pct: 4,
              earnings: true,
              grade: "A",
              setup_state: null,
            },
          ],
        },
        sweep: null,
      },
    });
    renderWithClient(<LiveBoard />);
    const table = await screen.findByRole("table", { name: /Setups near or past their pivot/ });
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    const first = within(table).getAllByRole("row")[1];
    expect(first).toHaveTextContent("SPOT");
    expect(first).toHaveTextContent("92.65");
    expect(first).toHaveTextContent("Breaking out (provisional)");
    expect(first).toHaveTextContent("10:15:30 ET");
    expect(screen.getByText(/Live from replay/)).toBeInTheDocument();
    const movers = screen.getByRole("region", { name: "Pre-market movers" });
    expect(movers).toHaveTextContent("NVDA");
    expect(movers).toHaveTextContent("◆ earnings");
  });

  it("formats intraday times in US/Eastern and buckets live prints", () => {
    const t = Date.UTC(2026, 9, 2, 14, 15, 30) / 1000;
    expect(etClock(t)).toBe("10:15");
    expect(bucketOf("2026-10-02T14:17:42Z", 5)).toBe(Date.UTC(2026, 9, 2, 14, 15) / 1000);
    expect(bucketOf("2026-10-02T14:17:42Z", 1)).toBe(Date.UTC(2026, 9, 2, 14, 17) / 1000);
  });
});
