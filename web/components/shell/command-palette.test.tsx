import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SearchHit } from "@/lib/api";
import { mockApi, renderWithClient } from "@/test-utils";
import { CommandPalette, currentSymbol } from "./command-palette";

const push = vi.fn();
const prefetch = vi.fn();
let pathname = "/";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, prefetch }),
  usePathname: () => pathname,
}));

afterEach(() => {
  vi.unstubAllGlobals();
  push.mockReset();
  pathname = "/";
});

const SPOT: SearchHit = {
  symbol: "SPOT",
  name: "Spotify Technology S.A.",
  exchange: "NYSE",
  type: "common",
  date: "2026-10-02",
  close: 94,
  change_pct: 1.35,
  grade: "A",
  score: 84.2,
  state: "breakout",
};

function Harness({ initial = false }: { initial?: boolean }) {
  const [open, setOpen] = useState(initial);
  return <CommandPalette open={open} onOpenChange={setOpen} />;
}

describe("CommandPalette", () => {
  it("opens with ⌘K, searches as you type and opens the stock", async () => {
    mockApi({ "GET /api/search?q=spo": { body: [SPOT] } });
    renderWithClient(<Harness />);
    expect(screen.queryByPlaceholderText(/Search a ticker/)).not.toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }));
    });
    const input = await screen.findByPlaceholderText(/Search a ticker/);
    fireEvent.change(input, { target: { value: "spo" } });

    const option = await screen.findByRole("option", { name: /SPOT/ });
    expect(option).toHaveTextContent("Spotify Technology S.A.");
    expect(option).toHaveTextContent("94.00");
    expect(option).toHaveTextContent("▲ +1.35%");
    expect(option).toHaveTextContent("Breakout");
    fireEvent.click(option);
    expect(push).toHaveBeenCalledWith("/stocks/SPOT");
  });

  it("says what to try when nothing matches", async () => {
    mockApi({ "GET /api/search?q=zzz": { body: [] } });
    renderWithClient(<Harness initial />);
    fireEvent.change(await screen.findByPlaceholderText(/Search a ticker/), {
      target: { value: "zzz" },
    });
    await waitFor(() => expect(screen.getByText(/No stock matches “zzz”/)).toBeInTheDocument());
  });

  it("offers adding the stock being viewed to the watchlist", async () => {
    pathname = "/stocks/SPOT";
    renderWithClient(<Harness initial />);
    expect(
      await screen.findByRole("option", { name: /Add SPOT to watchlist/ }),
    ).toBeInTheDocument();
  });
});

describe("currentSymbol", () => {
  it("reads the stock page's symbol", () => {
    expect(currentSymbol("/stocks/spot")).toBe("SPOT");
    expect(currentSymbol("/screener")).toBeNull();
    expect(currentSymbol(null)).toBeNull();
  });
});
