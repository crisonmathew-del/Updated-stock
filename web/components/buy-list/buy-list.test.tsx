import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useListStore } from "@/stores/list";
import { mockApi, renderWithClient } from "@/test-utils";
import { BuyListPanel, BuyListView, TrackRecord } from "./buy-list";
import { BUY_LIST_DATA } from "./fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useListStore.setState({ source: null, symbols: [] });
});

describe("BuyListView", () => {
  it("says what the market allows, the order to place and what's on the watch", async () => {
    mockApi({ "GET /api/buy-list": { body: BUY_LIST_DATA } });
    renderWithClient(<BuyListView />);

    const market = await screen.findByRole("region", { name: "The market today" });
    expect(market).toHaveTextContent("Market: Confirmed uptrend");
    expect(market).toHaveTextContent("the rules allow new buys");

    const orders = screen.getByRole("list", { name: "Orders to place" });
    const spot = within(orders).getByRole("listitem");
    expect(spot).toHaveTextContent("SPOT");
    expect(spot).toHaveTextContent("Get ready");
    expect(spot).toHaveTextContent("Your order for the next session");
    expect(spot).toHaveTextContent(
      "Place a buy-stop order for 259 shares at 92.56 (limit 97.08), then a stop-loss at 88.71.",
    );

    const watch = screen.getByRole("list", { name: "Stocks on the breakout watch" });
    const rows = within(watch).getAllByRole("listitem", { name: "" });
    expect(rows[0]).toHaveTextContent("SPOT");
    expect(rows[0]).toHaveTextContent("78/100 ready");
    const crwd = rows.find((r) => r.textContent?.includes("CRWD")) as HTMLElement;
    expect(crwd).toHaveTextContent("4.2% below");
    expect(crwd).toHaveTextContent("Don't buy");
    expect(crwd).toHaveTextContent("Not a buy yet: results due in 4 sessions.");

    // Opening a stock makes the list it came from the one [ / ] flip through.
    fireEvent.click(within(watch).getAllByRole("link")[1]);
    expect(useListStore.getState()).toMatchObject({
      source: "Breakout watch",
      symbols: ["SPOT", "CRWD"],
    });
  });

  it("explains an empty list when the market holds buys back", async () => {
    mockApi({
      "GET /api/buy-list": {
        body: {
          ...BUY_LIST_DATA,
          market: {
            state: "correction",
            label: "Correction",
            words: "in a correction",
            allows_buys: false,
            note: "No new buys while the market is in a correction.",
          },
          orders: [],
          watch: [],
        },
      },
    });
    renderWithClient(<BuyListView />);
    expect(
      await screen.findByText(/No orders tonight: the market rule says no new buys/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Nothing on the watch tonight/)).toBeInTheDocument();
  });
});

describe("TrackRecord", () => {
  it("counts hits, misses and picks still waiting", () => {
    renderWithClient(<TrackRecord track={BUY_LIST_DATA.track} />);
    const text = screen.getByText(/Track record since Mar 3/);
    expect(text).toHaveTextContent(
      "Track record since Mar 3: 12 picks. ✓ 4 broke out within 10 sessions, ✕ 6 didn’t, 2 still waiting. Hit rate 40% (a median of 3 sessions to the breakout). Past hits don’t promise future ones.",
    );
  });

  it("says when there's no record yet", () => {
    renderWithClient(
      <TrackRecord
        track={{
          ...BUY_LIST_DATA.track,
          picks: 0,
          hits: 0,
          misses: 0,
          pending: 0,
          hit_rate_pct: null,
          median_sessions_to_breakout: null,
          since: null,
        }}
      />,
    );
    expect(screen.getByText(/No track record yet/)).toHaveTextContent(
      "a pick counts as a hit if it breaks out within 10 sessions.",
    );
  });

  it("has no hit rate until a pick has had time to break out", () => {
    renderWithClient(
      <TrackRecord
        track={{
          ...BUY_LIST_DATA.track,
          picks: 2,
          hits: 0,
          misses: 0,
          pending: 2,
          hit_rate_pct: null,
          median_sessions_to_breakout: null,
        }}
      />,
    );
    expect(screen.getByText(/2 picks/)).toHaveTextContent(
      "No hit rate yet: no pick has had 10 sessions to break out.",
    );
  });
});

describe("BuyListPanel", () => {
  it("shows the orders and the top of the watch on the dashboard", async () => {
    mockApi({ "GET /api/buy-list": { body: BUY_LIST_DATA } });
    renderWithClient(<BuyListPanel />);
    const orders = await screen.findByRole("list", { name: "Orders to place" });
    expect(orders).toHaveTextContent("SPOT");
    expect(orders).toHaveTextContent("92.56");
    expect(orders).toHaveTextContent("259 shares");
    expect(
      screen.getByText("2 on the breakout watch, most ready: SPOT, CRWD."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the Buy list →" })).toHaveAttribute(
      "href",
      "/buy-list",
    );
  });
});
