import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderWithClient } from "@/test-utils";
import { AiSummaryPanel } from "./ai-summary";

const SUMMARY = {
  thesis: "SPOT sits 0.6% under its 92.46 pivot in a four-contraction VCP.",
  catalyst: "No news source is connected.",
  risks: ["Earnings in 4 sessions.", "Stop at 88.71."],
  unverified: ["120"],
  model: "claude-opus-5-5",
  generated_at: "2026-10-06T20:00:00Z",
  as_of: "2026-10-02",
  cached: false,
};

afterEach(() => vi.unstubAllGlobals());

describe("AI summary", () => {
  it("says how to switch it on when the server has no key", async () => {
    mockApi({
      "GET /api/stocks/SPOT/ai-summary": {
        body: { enabled: false, model: "claude-opus-5-5", summary: null },
      },
    });
    renderWithClient(<AiSummaryPanel symbol="SPOT" />);
    expect(await screen.findByText(/ANTHROPIC_API_KEY/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("writes one on request, labelled, with unverified numbers called out", async () => {
    mockApi({
      "GET /api/stocks/SPOT/ai-summary": {
        body: { enabled: true, model: "claude-opus-5-5", summary: null },
      },
      "POST /api/stocks/SPOT/ai-summary": {
        body: { enabled: true, model: "claude-opus-5-5", summary: SUMMARY },
      },
    });
    renderWithClient(<AiSummaryPanel symbol="SPOT" />);
    fireEvent.click(await screen.findByRole("button", { name: "Summarise" }));
    expect(await screen.findByText(SUMMARY.thesis)).toBeInTheDocument();
    expect(screen.getByText("Stop at 88.71.")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("aren't in the data it was given: 120");
    expect(screen.getByText(/Written by AI from the data on this page/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Write again" })).toBeInTheDocument();
  });
});
