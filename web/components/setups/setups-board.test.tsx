import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DETAIL, LIST } from "./fixtures";
import { mockApi, renderWithClient } from "@/test-utils";
import { SetupsBoard } from "./setups-board";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SetupsBoard", () => {
  it("lists setups with their plan and opens the breakdown", async () => {
    mockApi({
      "GET /api/setups?sort=score&limit=200": { body: LIST },
      "GET /api/setups/5": { body: DETAIL },
    });
    renderWithClient(<SetupsBoard />);

    const row = (await screen.findByRole("button", { name: /SPOT/ })).closest("tr")!;
    expect(row).toHaveTextContent("Volatility contraction (VCP)");
    expect(row).toHaveTextContent("Near pivot");
    expect(row).toHaveTextContent("1.0% below");
    expect(row).toHaveTextContent("92.56 / 88.71");
    expect(row).toHaveTextContent("⚠ Earnings risk");
    expect(screen.getByRole("button", { name: "Watch 3" })).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: /SPOT/ }));
    const detail = await screen.findByLabelText("SPOT setup details");
    expect(within(detail).getByText("Trend Template & stage")).toBeInTheDocument();
    expect(within(detail).getByLabelText("no data, left out")).toBeInTheDocument();
    expect(detail).toHaveTextContent("259 (23.97% of the account)");
    expect(detail).toHaveTextContent("100.26 / 104.11");
    expect(detail).toHaveTextContent("New volatility contraction (VCP) setup");
  });

  it("filters by stage", async () => {
    const fetchMock = mockApi({
      "GET /api/setups?sort=score&limit=200": { body: LIST },
      "GET /api/setups?sort=score&limit=200&state=breakout": {
        body: { ...LIST, total: 0, items: [] },
      },
    });
    renderWithClient(<SetupsBoard />);
    fireEvent.click(await screen.findByRole("button", { name: /^Breakout/ }));
    expect(await screen.findByText("No setups match.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/setups?sort=score&limit=200&state=breakout",
      expect.anything(),
    );
  });
});
