import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProvidersStatus, SettingItem } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { SettingsPage, show } from "./settings-page";

const ITEMS: SettingItem[] = [
  {
    key: "account_size",
    category: "risk",
    description: "Account size",
    value: 100000,
    default: 100000,
    constraints: { type: "number", exclusiveMinimum: 0 },
  },
  {
    key: "risk_per_trade_pct",
    category: "risk",
    description: "Risk per trade (% of account)",
    value: 0.5,
    default: 1,
    constraints: { type: "number", exclusiveMinimum: 0, maximum: 10 },
  },
  {
    key: "backtest_trailing_exit",
    category: "backtest",
    description: "Sell on a close below this average",
    value: "sma50",
    default: "sma50",
    constraints: { enum: ["sma50", "ema21", "none"], type: "string" },
  },
];

const KEYS: ProvidersStatus = {
  providers: {
    prices: "yfinance",
    stream: "none",
    fundamentals: "sec_edgar",
    news: "none",
    email: "resend",
  },
  keys: [
    {
      name: "Anthropic",
      env: ["ANTHROPIC_API_KEY"],
      configured: true,
      in_use: true,
      purpose: "AI summaries",
    },
    {
      name: "Resend",
      env: ["RESEND_API_KEY"],
      configured: false,
      in_use: true,
      purpose: "Alert emails",
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("Settings", () => {
  it("edits, saves and resets settings, and shows which keys are set", async () => {
    const saved = ITEMS.map((i) => (i.key === "account_size" ? { ...i, value: 50000 } : i));
    const fetchMock = mockApi({
      "GET /api/settings": { body: { items: ITEMS } },
      "GET /api/settings/keys": { body: KEYS },
      "GET /api/admin/data-health": {
        body: {
          summary: { critical: 0, warning: 0, info: 0 },
          by_check: {},
          issues: [],
          last_check: null,
        },
      },
      "PATCH /api/settings": { body: { items: saved } },
      "POST /api/settings/reset": {
        body: { items: ITEMS.map((i) => ({ ...i, value: i.default })) },
      },
    });
    renderWithClient(<SettingsPage />);
    const keys = await screen.findByRole("table", { name: /whether its key is set/ });
    expect(
      within(keys)
        .getByRole("rowheader", { name: /Anthropic/ })
        .closest("tr"),
    ).toHaveTextContent("Configured");
    expect(
      within(keys)
        .getByRole("rowheader", { name: /Resend/ })
        .closest("tr"),
    ).toHaveTextContent("Missing (selected)");

    const risk = await screen.findByLabelText("Risk per trade (% of account)");
    expect(screen.getByText("Default: 1 · 0–10")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Account size"), { target: { value: "50000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save 1 change" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "No changes" })).toBeDisabled());
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(patch?.[1]?.body as string)).toEqual({ changes: { account_size: 50000 } });

    expect(risk).toHaveValue(0.5);
    fireEvent.click(screen.getByRole("button", { name: /Reset “Risk per trade/ }));
    await waitFor(() =>
      expect(screen.getByLabelText("Risk per trade (% of account)")).toHaveValue(1),
    );
    const reset = fetchMock.mock.calls.find(([url]) => url === "/api/settings/reset");
    expect(JSON.parse(reset?.[1]?.body as string)).toEqual({ keys: ["risk_per_trade_pct"] });

    fireEvent.click(screen.getByRole("button", { name: /Backtest lab/ }));
    expect(screen.getByLabelText("Sell on a close below this average")).toHaveValue("sma50");
  });

  it("writes values in words", () => {
    expect(show([100, 140])).toBe("100, 140");
    expect(show({ confirmed_uptrend: 1 })).toBe("Confirmed uptrend 1");
    expect(show(true)).toBe("On");
    expect(show("")).toBe("(empty)");
  });
});
