import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DataHealth } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { DataHealthPanel } from "./data-health-panel";

const ISSUE = {
  first_detected_at: "2026-10-02T21:00:00Z",
  last_detected_at: "2026-10-02T21:00:00Z",
};

const healthy: DataHealth = {
  summary: { critical: 0, warning: 1, info: 2 },
  by_check: {},
  issues: [
    {
      id: 1,
      check: "unexplained_move",
      severity: "warning",
      symbol: "XYZ",
      issue_date: "2026-09-14",
      detail: "+62% close-to-close with no split on record; verify",
      ...ISSUE,
    },
  ],
  last_check: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DataHealthPanel", () => {
  it("says plainly when there are no critical issues", async () => {
    mockApi({ "GET /api/admin/data-health": { body: healthy } });
    renderWithClient(<DataHealthPanel />);

    expect(await screen.findByText("No critical issues")).toBeInTheDocument();
    expect(screen.getByText("1 warning")).toBeInTheDocument();
    const row = screen.getByText("XYZ").closest("tr");
    expect(row).toHaveTextContent("Unexplained move");
    expect(row).toHaveTextContent("+62% close-to-close with no split on record; verify");
  });

  it("leads with critical problems and filters by severity", async () => {
    const critical: DataHealth = {
      ...healthy,
      summary: { critical: 1, warning: 0, info: 0 },
      issues: [
        {
          id: 2,
          check: "dataset_stale",
          severity: "critical",
          symbol: null,
          issue_date: "2026-10-02",
          detail: "No bars for 2026-10-02 yet (latest is 2026-09-25).",
          ...ISSUE,
        },
      ],
    };
    const fetchMock = mockApi({
      "GET /api/admin/data-health": { body: critical },
      "GET /api/admin/data-health?severity=info": { body: { ...critical, issues: [] } },
    });
    renderWithClient(<DataHealthPanel />);

    expect(await screen.findByText("1 critical")).toBeInTheDocument();
    expect(screen.queryByText("No critical issues")).not.toBeInTheDocument();
    expect(screen.getByText("Dataset stale")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Info" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/admin/data-health?severity=info",
        expect.anything(),
      ),
    );
    expect(await screen.findByText("No open info issues.")).toBeInTheDocument();
  });
});
