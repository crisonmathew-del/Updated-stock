import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Fundamentals } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { FundamentalsSection } from "./fundamentals-section";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ACME: Fundamentals = {
  symbol: "ACME",
  as_of: "2023-11-15",
  refreshed_at: "2023-11-14T11:00:00Z",
  grade: {
    date: "2023-11-15",
    grade: "A",
    score: 92.6,
    path: "eps",
    basis: "quarterly",
    coverage_pct: 100,
    components: [
      {
        key: "eps_growth",
        label: "EPS growth",
        points: 25,
        max_points: 25,
        status: "pass",
        detail:
          "Q3 FY2023 EPS $0.63 vs $0.45 a year earlier: +40.0% (needs 25%, full credit at 40%).",
        bonus: false,
      },
      {
        key: "accumulation",
        label: "Accumulation (up/down volume)",
        points: 5,
        max_points: 10,
        status: "partial",
        detail: "50-day up/down volume ratio 1.10 (accumulation at 1.2, neutral at 1.0).",
        bonus: false,
      },
      {
        key: "insider_bonus",
        label: "Insider cluster buying",
        points: 0,
        max_points: 5,
        status: "fail",
        detail: "0 officer/director open-market buyer(s) in the last 30 days; a cluster needs 2.",
        bonus: true,
      },
    ],
  },
  quarters: [
    {
      period_end: "2023-09-30",
      label: "Q3 FY2023",
      reported_date: "2023-10-30",
      eps: 0.63,
      revenue: 140e6,
      net_income: 6.3e6,
      eps_growth_pct: 40,
      eps_note: null,
      revenue_growth_pct: 27.3,
      derived: false,
      currency: "USD",
    },
    {
      period_end: "2022-12-31",
      label: "Q4 FY2022",
      reported_date: "2023-02-15",
      eps: 0.1,
      revenue: 120e6,
      net_income: 1e6,
      eps_growth_pct: null,
      eps_note: "turnaround",
      revenue_growth_pct: null,
      derived: true,
      currency: "USD",
    },
  ],
  years: [],
  earnings: [
    { report_date: "2024-01-25", status: "estimated", timing: "unknown" },
    { report_date: "2023-10-26", status: "reported", timing: "after_close" },
  ],
  insiders: [
    {
      transaction_date: "2023-11-01",
      filed_date: "2023-11-03",
      insider_name: "Doe Jane",
      role: "Chief Executive Officer",
      code: "P",
      shares: 5000,
      price: 41.2,
    },
  ],
};

describe("FundamentalsSection", () => {
  it("explains the grade and shows the quarters as reported", async () => {
    mockApi({ "GET /api/stocks/ACME/fundamentals": { body: ACME } });
    renderWithClient(<FundamentalsSection symbol="ACME" />);

    expect(await screen.findByLabelText("Grade A")).toBeInTheDocument();
    expect(
      screen.getByText(/93\/100 · EPS path · quarterly data · 100% of points/),
    ).toBeInTheDocument();
    const accumulation = screen.getByText("Accumulation (up/down volume)").closest("li")!;
    expect(accumulation).toHaveTextContent("Partial:");
    expect(accumulation).toHaveTextContent("5.0/10");
    expect(screen.getByText("Insider cluster buying").closest("li")).toHaveTextContent("(bonus)");

    const table = screen.getByRole("table", { name: /Quarters/ });
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Q3 FY2023");
    expect(rows[1]).toHaveTextContent("+40.0%");
    expect(rows[1]).toHaveTextContent("140M");
    expect(rows[2]).toHaveTextContent("turnaround");
    expect(rows[2]).toHaveTextContent("†"); // derived Q4

    expect(screen.getByText(/2024-01-25 \(estimated from last year\)/)).toBeInTheDocument();
    expect(screen.getByText(/2023-10-26 \(after close\)/)).toBeInTheDocument();
    expect(screen.getByText(/▲ Bought/).closest("li")).toHaveTextContent(
      "5,000 at 41.20 · Doe Jane",
    );
  });

  it("says when there isn't enough data for a grade", async () => {
    mockApi({
      "GET /api/stocks/ACME/fundamentals": {
        body: {
          ...ACME,
          grade: { ...ACME.grade, grade: null, score: null, coverage_pct: 20 },
          quarters: [],
        },
      },
    });
    renderWithClient(<FundamentalsSection symbol="ACME" />);
    expect(await screen.findByLabelText("Grade n/a")).toBeInTheDocument();
    expect(screen.getByText(/20% of points have data/)).toBeInTheDocument();
  });
});
