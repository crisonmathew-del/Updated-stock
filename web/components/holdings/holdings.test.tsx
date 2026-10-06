import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLive } from "@/stores/live";
import { mockApi, renderWithClient } from "@/test-utils";
import { HOLDING, QUOTE } from "./fixtures";
import { Holdings } from "./holdings";

let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/holdings",
  useSearchParams: () => new URLSearchParams(search),
}));

const SETTINGS = {
  items: [
    { key: "account_size", value: 100000 },
    { key: "risk_per_trade_pct", value: 1 },
    { key: "max_position_pct", value: 25 },
    { key: "max_stop_loss_pct", value: 8 },
    { key: "profit_take_min_pct", value: 20 },
  ],
};

beforeEach(() => {
  search = "";
  useLive.setState({ quotes: {}, events: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Holdings", () => {
  it("shows positions with P&L in R and their sell rules, live when quoted", async () => {
    const warned = {
      ...HOLDING,
      warnings: [
        {
          rule: "breakeven",
          priority: "normal" as const,
          title: "Raise your SPOT stop to breakeven",
          body: "…",
        },
      ],
    };
    mockApi({ "GET /api/holdings": { body: [warned] }, "GET /api/settings": { body: SETTINGS } });
    renderWithClient(<Holdings />);
    const table = await screen.findByRole("table", { name: "Your positions" });
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("SPOT");
    expect(row).toHaveTextContent("+1.50R");
    expect(row).toHaveTextContent("+$600");
    expect(row).toHaveTextContent("Raise your stop to breakeven");
    expect(screen.getByText("Risk to stops").nextSibling).toHaveTextContent("$800");

    // A live quote re-prices the row: (104.20 − 100) ÷ 8 = +0.53R.
    useLive.setState({ quotes: { SPOT: QUOTE } });
    await waitFor(() => expect(within(table).getAllByRole("row")[1]).toHaveTextContent("+0.53R"));
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("live");
  });

  it("sizes a new position from the account settings and moves a stop", async () => {
    search = "symbol=nvda&entry=100&stop=94";
    const fetchMock = mockApi({
      "GET /api/holdings": { body: [HOLDING] },
      "GET /api/settings": { body: SETTINGS },
      "POST /api/holdings": {
        status: 201,
        body: { ...HOLDING, id: 2, symbol: "NVDA", shares: 166 },
      },
      "PATCH /api/holdings/1": { body: { ...HOLDING, stop: 100 } },
    });
    renderWithClient(<Holdings />);
    const form = screen.getByRole("form", { name: "Add a position" });
    // $1,000 of risk ÷ $6 a share = 166 shares ($16,600, under the 25% cap).
    expect(await within(form).findByText(/166 shares risk \$996/)).toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "Add position" }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
        symbol: "NVDA",
        entry_price: 100,
        initial_stop: 94,
        shares: 166,
      });
    });

    fireEvent.click(await screen.findByRole("button", { name: "Move stop" }));
    const input = screen.getByLabelText("New stop for SPOT");
    fireEvent.change(input, { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Set" }));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
      expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ stop: 100 });
    });
  });
});
