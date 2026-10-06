import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderWithClient } from "@/test-utils";
import { DETAIL, OPTIONS_BODY, RUN, TRADES } from "./fixtures";
import { progressText } from "./format";
import { RunForm } from "./new-run";
import { BacktestReportView, heatStyle } from "./report";
import { RunList } from "./run-list";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push, prefetch: vi.fn() }),
  usePathname: () => "/backtests",
  useSearchParams: () => new URLSearchParams(),
}));
// The canvas charts don't run in jsdom; their data wiring is checked through the props.
vi.mock("./equity-chart", () => ({
  default: ({ split }: { split: string | null }) => (
    <div>Equity chart, out of sample from {split}</div>
  ),
}));
vi.mock("./trade-chart", () => ({
  default: ({ data }: { data: { symbol: string } }) => <div>Chart of {data.symbol}</div>,
}));

afterEach(() => {
  vi.unstubAllGlobals();
  push.mockReset();
});

describe("Backtest lab", () => {
  it("lists runs with their headline numbers and what a running one is doing", async () => {
    const running = {
      ...RUN,
      id: 4,
      name: "With the heatmap",
      status: "running" as const,
      summary: null,
      progress: { stage: "tape", done: 150, total: 600 },
    };
    mockApi({ "GET /api/backtests": { body: [running, RUN] } });
    renderWithClient(<RunList />);
    const table = await screen.findByRole("table", { name: "Backtest runs, newest first" });
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Running");
    expect(rows[1]).toHaveTextContent("Replaying the scan: 150 of 600 stocks (25%)");
    expect(rows[2]).toHaveTextContent("▲ +5.6%");
    expect(rows[2]).toHaveTextContent("8.1%"); // SPY
    expect(rows[2]).toHaveTextContent("+1.10R");
    expect(within(rows[1]).queryByRole("button", { name: /Delete/ })).toBeNull();
  });

  it("starts a run with the rules from the form and opens it", async () => {
    const fetchMock = mockApi({ "POST /api/backtests": { status: 202, body: { ...RUN, id: 9 } } });
    renderWithClient(<RunForm options={OPTIONS_BODY} />);
    fireEvent.change(screen.getByLabelText("Grade at least"), { target: { value: "B" } });
    fireEvent.click(screen.getByLabelText("Flat base"));
    fireEvent.change(screen.getByLabelText("Saved screen"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Positions at once"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Trailing exit"), { target: { value: "ema21" } });
    fireEvent.click(screen.getByLabelText(/Sensitivity heatmap/));
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/backtests/9"));
    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(body).toMatchObject({
      start: "2021-10-04",
      end: "2026-10-02",
      rules: { min_grade: "B", patterns: ["flat_base"], skip_risk_too_wide: true },
      portfolio: { max_positions: 5, risk_pct: 1, initial_capital: 100000 },
      exits: { trailing: "ema21", sell_unconfirmed: true, time_stop_sessions: 15 },
      sensitivity: true,
      screen_id: 7,
    });
  });

  it("refuses a second run while one is going", async () => {
    mockApi({
      "POST /api/backtests": {
        status: 409,
        body: { detail: "A backtest is already running. Wait for it to finish." },
      },
    });
    renderWithClient(<RunForm options={{ ...OPTIONS_BODY, running: 4 }} />);
    expect(screen.getByRole("button", { name: "Run backtest" })).toBeDisabled();
    expect(screen.getByText(/A backtest is running/)).toBeInTheDocument();
  });

  it("shows the report: labels, numbers, samples, heatmap, trades and a trade's chart", async () => {
    mockApi({
      "GET /api/backtests/3": { body: DETAIL },
      "GET /api/backtests/3/trades/1/chart": {
        body: {
          symbol: "SPOT",
          time: [],
          open: [],
          high: [],
          low: [],
          close: [],
          sma50: [],
          trade: TRADES[0],
        },
      },
    });
    renderWithClient(<BacktestReportView id={3} />);
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("Hypothetical.");
    expect(note).toHaveTextContent("Survivorship bias.");
    const headline = screen.getByRole("group", { name: "Headline numbers" });
    expect(within(headline).getByText("CAGR").nextSibling).toHaveTextContent("▲ +5.6%");
    expect(within(headline).getByText("Expectancy").nextSibling).toHaveTextContent("+1.10R");
    expect(
      await screen.findByText("Equity chart, out of sample from 2025-04-01"),
    ).toBeInTheDocument();

    const samples = screen.getByRole("table", { name: "In-sample against out-of-sample results" });
    expect(within(samples).getByText("CAGR").parentElement).toHaveTextContent("5.6%−1.5%");

    const heat = screen.getByRole("table", { name: /by VCP final contraction/ });
    const own = within(heat).getByText("(your settings)").parentElement;
    expect(own).toHaveTextContent("5.6%");
    fireEvent.click(screen.getByRole("radio", { name: "Trades" }));
    expect(screen.getByRole("table", { name: /Trades by VCP/ })).toBeInTheDocument();

    const trades = screen.getByRole("table", { name: "Every simulated trade" });
    const rows = within(trades).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("SPOT");
    expect(rows[1]).toHaveTextContent("Closed below the 50-day SMA (after a partial sale)");
    expect(rows[1]).toHaveTextContent("+4.27R");
    expect(rows[2]).toHaveTextContent("▼ −4.1%");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "SPOT" }));
    expect(await screen.findByText("Chart of SPOT")).toBeInTheDocument();
    expect(screen.getByText(/sold 84 at 111.18 \(Partial profit/)).toBeInTheDocument();
    expect(screen.getByText("Confirmed uptrend")).toBeInTheDocument();
  });

  it("describes progress and failures in words; colours carry the sign", () => {
    expect(progressText({ status: "queued", progress: {}, error: null })).toBe(
      "Waiting for the worker…",
    );
    expect(progressText({ status: "running", progress: { stage: "simulate" }, error: null })).toBe(
      "Simulating the portfolio…",
    );
    expect(progressText({ status: "failed", progress: {}, error: "No analytics yet." })).toBe(
      "Failed: No analytics yet.",
    );
    expect(heatStyle("cagr_pct", 5, 10).backgroundColor).toContain("var(--rise) 34%");
    expect(heatStyle("cagr_pct", -10, 10).backgroundColor).toContain("var(--fall) 55%");
    expect(heatStyle("cagr_pct", null, 10)).toEqual({});
  });
});
