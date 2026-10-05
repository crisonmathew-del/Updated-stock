import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderWithClient } from "@/test-utils";
import { DETAIL } from "@/components/setups/fixtures";
import { PlanCard, ScoreCard } from "./score-plan";

afterEach(() => {
  vi.unstubAllGlobals();
});

const SETTINGS = {
  items: [
    { key: "account_size", value: 100000 },
    { key: "risk_per_trade_pct", value: 1 },
    { key: "max_position_pct", value: 25 },
    { key: "max_stop_loss_pct", value: 8 },
    { key: "profit_take_min_pct", value: 20 },
    { key: "account_currency", value: "USD" },
  ],
};

describe("PlanCard", () => {
  it("shows the scan's plan and resizes live when the stop is edited", async () => {
    mockApi({ "GET /api/settings": { body: SETTINGS } });
    renderWithClient(<PlanCard setup={DETAIL} />);
    const card = screen.getByRole("region", { name: "Trade plan" });
    expect(await within(card).findByText(/259/)).toHaveTextContent("259 (23.97% of account)");
    expect(card).toHaveTextContent("USD 997.15");
    expect(card).toHaveTextContent("100.26 / 104.11");

    // A tighter stop: risk 92.56 − 90.56 = 2.00 → 1,000 / 2 = 500 shares, but the 25% cap
    // allows 25,000 / 92.56 = 270.
    fireEvent.change(within(card).getByLabelText("Stop"), { target: { value: "90.56" } });
    expect(card).toHaveTextContent("270 (24.99% of account, capped)");
    expect(card).toHaveTextContent("edited (not saved)");

    fireEvent.change(within(card).getByLabelText("Stop"), { target: { value: "95" } });
    expect(within(card).getByRole("alert")).toHaveTextContent(
      "The stop must be below the entry, and both must be positive numbers.",
    );
    fireEvent.click(within(card).getByRole("button", { name: /Reset to the scan/ }));
    expect(within(card).getByLabelText("Stop")).toHaveValue("88.71");
  });
});

describe("ScoreCard", () => {
  it("shows each part with its status and the final arithmetic", () => {
    renderWithClient(<ScoreCard setup={DETAIL} />);
    const card = screen.getByRole("region", { name: "Setup score" });
    expect(card).toHaveTextContent("83.5 × 1 regime = 83.5");
    expect(within(card).getByLabelText("passes")).toBeInTheDocument();
    expect(within(card).getByLabelText("no data")).toBeInTheDocument();
    expect(card).toHaveTextContent("Earnings risk");
  });
});
