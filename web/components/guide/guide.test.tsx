import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SettingItem } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { Guide } from "./guide";

afterEach(() => {
  vi.unstubAllGlobals();
});

function setting(key: string, value: unknown): SettingItem {
  return { key, category: "decisions", description: "", value, default: value, constraints: {} };
}

describe("Guide", () => {
  it("quotes the account's own rules and explains every term", async () => {
    mockApi({
      "GET /api/settings": {
        body: {
          items: [
            setting("decision_min_rs_rating", 90),
            setting("decision_regimes", "confirmed_or_under_pressure"),
            setting("decision_earnings_block_sessions", 0),
            setting("account_size", 50_000),
          ],
        },
      },
    });
    renderWithClient(<Guide />);
    const rules = screen.getByRole("region", { name: "The 7 rules for a buy" });
    expect(await within(rules).findByText(/An RS Rating of 90 or more/)).toBeInTheDocument();
    expect(rules).toHaveTextContent(
      "New buys in a confirmed uptrend, or at 50% of the size when the uptrend is under pressure.",
    );
    expect(rules).toHaveTextContent("Earnings: Ignored (switched off in Settings).");
    expect(within(rules).getAllByRole("listitem")).toHaveLength(7);

    const steps = screen.getByRole("region", { name: "Every day, in 6 steps" });
    expect(steps).toHaveTextContent("costs about 1% of your account (500 of 50,000");
    expect(steps).toHaveTextContent(/16:00 New York time \((20|21):00 (BST|GMT) for you\)/);

    const terms = screen.getByRole("region", { name: "Every term, in plain English" });
    expect(within(terms).getByText("Buy-stop order")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "buy-stop order" })).toHaveAttribute(
      "href",
      "#term-buy_stop",
    );
  });
});
