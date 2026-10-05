import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BackfillProgress } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { BackfillPanel } from "./backfill-panel";

function progress(overrides: Partial<BackfillProgress>): BackfillProgress {
  return {
    status: "running",
    run_id: 7,
    total: 5_000,
    processed: 1_250,
    done: 1_200,
    no_data: 40,
    failed: 10,
    bars_written: 3_024_000,
    start: "2016-10-02",
    end: "2026-10-02",
    started_at: "2026-10-02T21:00:00Z",
    updated_at: "2026-10-02T21:12:00Z",
    message: null,
    current: ["MSFT", "NVDA"],
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BackfillPanel", () => {
  it("shows live progress with counts and the current batch", async () => {
    mockApi({ "GET /api/admin/backfill": { body: progress({}) } });
    renderWithClient(<BackfillPanel />);

    expect(await screen.findByText("Running · 1,250 of 5,000 tickers (25%)")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Backfill progress" })).toHaveAttribute(
      "aria-valuenow",
      "1250",
    );
    expect(screen.getByText("3,024,000")).toBeInTheDocument();
    expect(screen.getByText("Now fetching MSFT, NVDA")).toBeInTheDocument();
  });

  it("explains a stopped run and how to resume", async () => {
    const message =
      "RateLimitedError: Yahoo is still rate-limiting. Completed tickers are kept; run the backfill again to resume.";
    mockApi({ "GET /api/admin/backfill": { body: progress({ status: "failed", message }) } });
    renderWithClient(<BackfillPanel />);

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText(/Now fetching/)).not.toBeInTheDocument();
  });

  it("queues a job and reports a conflict in plain words", async () => {
    mockApi({
      "GET /api/admin/backfill": { body: progress({ status: "idle", total: 0, processed: 0 }) },
      "POST /api/admin/backfill": { status: 202, body: { job: "backfill", job_id: "backfill:1" } },
      "POST /api/admin/eod-update": {
        status: 409,
        body: { detail: "Another data job (universe, backfill or EOD update) is running." },
      },
    });
    renderWithClient(<BackfillPanel />);
    expect(await screen.findByText("No backfill has run yet.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run backfill" }));
    expect(await screen.findByText("Backfill queued.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run EOD update" }));
    expect(await screen.findByText(/Another data job .* is running/)).toBeInTheDocument();
  });
});
