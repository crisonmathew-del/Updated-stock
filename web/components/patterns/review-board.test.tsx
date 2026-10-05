import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Pattern, ReviewStats } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { ReviewBoard } from "./review-board";

afterEach(() => {
  vi.unstubAllGlobals();
});

const VCP: Pattern = {
  id: 7,
  symbol: "SPOT",
  name: "Spotify Technology S.A.",
  type: "vcp",
  type_label: "Volatility contraction (VCP)",
  timeframe: "daily",
  start_date: "2025-01-14",
  end_date: "2025-04-28",
  pivot: 92.46,
  base_low: 75.62,
  depth_pct: 24.8,
  duration_weeks: 14.4,
  quality: 71.5,
  base_number: 1,
  status: "forming",
  status_date: "2025-04-28",
  first_detected: "2025-04-21",
  last_seen: "2025-04-28",
  components: [
    {
      key: "depth",
      label: "Depth of the contractions",
      points: 15,
      max_points: 15,
      detail: "Contractions 24.8% → 13.9% → 7.0% → 4.0%",
    },
  ],
  swings: [],
  contractions: [],
  details: {},
  review: null,
};

const STATS: ReviewStats = {
  types: [
    {
      type: "vcp",
      type_label: "Volatility contraction (VCP)",
      detected: 12,
      reviewed: 4,
      correct: 3,
      wrong: 1,
      unsure: 0,
      false_positive_rate: 0.25,
    },
  ],
  reviewed: 4,
  false_positive_rate: 0.25,
};

describe("ReviewBoard", () => {
  it("shows the false-positive rate per type and the sample with charts", async () => {
    mockApi({
      "GET /api/admin/patterns/review-stats": { body: STATS },
      "GET /api/admin/patterns/sample?size=20&seed=1": { body: [VCP] },
    });
    renderWithClient(<ReviewBoard />);

    const row = (await screen.findByRole("cell", { name: "Volatility contraction (VCP)" })).closest(
      "tr",
    )!;
    expect(row).toHaveTextContent("12");
    expect(row).toHaveTextContent("25%");
    const card = await screen.findByRole("article", { name: "SPOT Volatility contraction (VCP)" });
    expect(
      within(card)
        .getByText(/quality/)
        .closest("p"),
    ).toHaveTextContent("Forming · quality 72/100");
    expect(within(card).getByRole("img")).toHaveAttribute("src", "/api/admin/patterns/7/chart.png");
    expect(within(card).getByText(/24.8% deep · pivot 92.46 · base 1/)).toBeInTheDocument();
  });

  it("records a verdict with its note and can clear it", async () => {
    const fetchMock = mockApi({
      "GET /api/admin/patterns/review-stats": { body: STATS },
      "GET /api/admin/patterns/sample?size=20&seed=1": {
        body: [
          {
            ...VCP,
            review: { verdict: "correct", note: null, reviewed_at: "2025-05-01T10:00:00Z" },
          },
        ],
      },
      "PUT /api/admin/patterns/7/review": { body: VCP },
      "DELETE /api/admin/patterns/7/review": { status: 204 },
    });
    renderWithClient(<ReviewBoard />);

    const group = await screen.findByRole("group", { name: "Verdict for SPOT" });
    expect(within(group).getByRole("button", { name: /Correct/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.change(screen.getByLabelText(/Note/), { target: { value: "Second leg too deep" } });
    fireEvent.click(within(group).getByRole("button", { name: /Wrong/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/admin/patterns/7/review",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ verdict: "wrong", note: "Second leg too deep" }),
        }),
      ),
    );
    fireEvent.click(within(group).getByRole("button", { name: "Clear" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/admin/patterns/7/review",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("draws another reproducible sample on request", async () => {
    const fetchMock = mockApi({
      "GET /api/admin/patterns/review-stats": {
        body: { types: [], reviewed: 0, false_positive_rate: null },
      },
      "GET /api/admin/patterns/sample?size=20&seed=1": { body: [] },
      "GET /api/admin/patterns/sample?size=20&seed=42&unreviewed=true": { body: [VCP] },
    });
    renderWithClient(<ReviewBoard />);

    expect(await screen.findByText(/Nothing detected yet/)).toBeInTheDocument();
    expect(await screen.findByText("No detections to review.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Sample"), { target: { value: "42" } });
    fireEvent.click(screen.getByLabelText("Only unreviewed"));
    fireEvent.click(screen.getByRole("button", { name: "Draw" }));
    expect(await screen.findByRole("article", { name: /SPOT/ })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/patterns/sample?size=20&seed=42&unreviewed=true",
      expect.anything(),
    );
  });
});
