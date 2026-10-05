import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SignalList } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { SignalLog } from "./signal-log";

afterEach(() => {
  vi.unstubAllGlobals();
});

const LIST: SignalList = {
  total: 2,
  counts: { breakout: 1, regime_change: 1 },
  items: [
    {
      id: 2,
      date: "2025-05-01",
      type: "breakout",
      type_label: "Breakout confirmed",
      symbol: "SPOT",
      name: "Spotify Technology S.A.",
      setup_id: 5,
      summary: "Breakout confirmed: Closed 0.3% above the pivot 92.46 on 330% of average volume.",
      price: 92.75,
      pivot: 92.46,
      entry: 92.56,
      stop: 88.71,
      score: 84.2,
      grade: "A",
      context: {},
      setup_state: "breakout",
      outcome: {
        sessions_observed: 1,
        returns: { "1": 1.35, "5": null, "10": null, "20": null, "60": null },
        returns_r: { "1": 0.37, "5": null, "10": null, "20": null, "60": null },
        mfe_pct: 1.9,
        mae_pct: -0.4,
        stop_hit_on: null,
        target_2r_on: null,
        gain_20_on: null,
        complete: false,
      },
    },
    {
      id: 1,
      date: "2025-04-30",
      type: "regime_change",
      type_label: "Market regime change",
      symbol: null,
      name: null,
      setup_id: null,
      summary: "Market regime: Correction → Confirmed uptrend.",
      price: 560.1,
      pivot: null,
      entry: null,
      stop: null,
      score: null,
      grade: null,
      context: {},
      outcome: null,
      setup_state: null,
    },
  ],
};

describe("SignalLog", () => {
  it("shows each signal with its outcome so far", async () => {
    mockApi({ "GET /api/signals?limit=200": { body: LIST } });
    renderWithClient(<SignalLog />);

    const row = (await screen.findByText("Breakout confirmed", { selector: "td" })).closest("tr")!;
    expect(row).toHaveTextContent("+1.4%");
    expect(row).toHaveTextContent("+0.37R");
    expect(row).toHaveTextContent("+1.9% / −0.4%");
    const market = screen.getByText("Market").closest("tr")!;
    expect(market).toHaveTextContent("not measured yet");
    expect(screen.getByRole("button", { name: "All 2" })).toBeInTheDocument();
  });

  it("filters by type", async () => {
    const fetchMock = mockApi({
      "GET /api/signals?limit=200": { body: LIST },
      "GET /api/signals?limit=200&type=breakout": {
        body: { ...LIST, items: [LIST.items[0]] },
      },
    });
    renderWithClient(<SignalLog />);
    fireEvent.click(await screen.findByRole("button", { name: "Breakout confirmed 1" }));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/signals?limit=200&type=breakout",
      expect.anything(),
    );
  });
});
