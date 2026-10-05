import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Watchlist, WatchlistItem } from "@/lib/api";
import { useListStore } from "@/stores/list";
import { mockApi, renderWithClient } from "@/test-utils";
import { Watchlists } from "./watchlists";

const replace = vi.fn();
const push = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push, prefetch: vi.fn() }),
  usePathname: () => "/watchlists",
  useSearchParams: () => new URLSearchParams(search),
}));

beforeEach(() => {
  search = "";
  useListStore.setState({ source: null, symbols: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
  push.mockReset();
});

function item(symbol: string, extra: Partial<WatchlistItem> = {}): WatchlistItem {
  return {
    symbol,
    name: `${symbol} Inc.`,
    position: 0,
    note: null,
    added_at: "2026-10-01T12:00:00Z",
    date: "2026-10-02",
    close: 94,
    change_pct: 1.35,
    rs_rating: 92,
    grade: null,
    score: null,
    state: null,
    pivot: null,
    readiness_pct: null,
    ...extra,
  };
}

const MAIN: Watchlist = {
  id: 1,
  name: "Watchlist",
  position: 0,
  items: [
    item("SPOT", {
      grade: "A",
      score: 84.2,
      state: "near_pivot",
      readiness_pct: 1.04,
      note: "VCP, 4T",
    }),
    item("AAPL"),
  ],
};
const EMPTY: Watchlist = { id: 2, name: "Earnings plays", position: 1, items: [] };

function mockLists(lists: Watchlist[] = [MAIN, EMPTY]) {
  return mockApi({
    "GET /api/watchlists": { body: lists },
    "PATCH /api/watchlists/1/items/AAPL": {
      body: { ...MAIN, items: [MAIN.items[0], { ...MAIN.items[1], note: "Wait for a base" }] },
    },
    "DELETE /api/watchlists/1/items/SPOT": { body: { ...MAIN, items: [MAIN.items[1]] } },
    "POST /api/watchlists/1/items": {
      status: 404,
      body: { detail: "No ticker NOPE in the universe." },
    },
    "POST /api/watchlists": {
      status: 201,
      body: { id: 3, name: "Leaders", position: 2, items: [] },
    },
    "PATCH /api/watchlists/1": { body: { ...MAIN, name: "Core" } },
  });
}

describe("Watchlists", () => {
  it("shows every list and opens the first, with its stocks and notes", async () => {
    mockLists();
    renderWithClient(<Watchlists />);

    const rows = await screen.findByRole("list", { name: "Stocks in Watchlist" });
    const links = within(rows).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/stocks/SPOT", "/stocks/AAPL"]);
    expect(within(rows).getByLabelText("Grade A, score 84")).toBeInTheDocument();
    expect(within(rows).getByText("1.0% below")).toBeInTheDocument();
    expect(screen.getByLabelText("Note for SPOT")).toHaveValue("VCP, 4T");
    const nav = screen.getByRole("navigation", { name: "Watchlists" });
    expect(within(nav).getByRole("button", { name: /Earnings plays/ })).toHaveTextContent("0");

    fireEvent.click(within(nav).getByRole("button", { name: /Earnings plays/ }));
    expect(replace).toHaveBeenCalledWith("/watchlists?list=2", { scroll: false });
  });

  it("opens the list named in the URL and says how to fill an empty one", async () => {
    search = "list=2";
    mockLists();
    renderWithClient(<Watchlists />);
    expect(await screen.findByText(/This list is empty/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Earnings plays/ })).toBeInTheDocument();
  });

  it("saves a note when the field is left, and removes a stock", async () => {
    const fetchMock = mockLists();
    renderWithClient(<Watchlists />);
    const note = await screen.findByLabelText("Note for AAPL");
    fireEvent.change(note, { target: { value: "  Wait for a base " } });
    fireEvent.blur(note);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/watchlists/1/items/AAPL",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ note: "Wait for a base" }),
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Note for AAPL")).toHaveValue("Wait for a base"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove SPOT from the list" }));
    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Stocks in Watchlist" })).getAllByRole("link"),
      ).toHaveLength(1),
    );
  });

  it("says why a symbol can't be added", async () => {
    mockLists();
    renderWithClient(<Watchlists />);
    fireEvent.change(await screen.findByLabelText("Symbol to add"), { target: { value: "nope" } });
    expect(screen.getByLabelText("Symbol to add")).toHaveValue("NOPE");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("No ticker NOPE in the universe.")).toBeInTheDocument();
  });

  it("creates and renames lists", async () => {
    mockLists();
    renderWithClient(<Watchlists />);
    fireEvent.click(await screen.findByRole("button", { name: "＋ New watchlist" }));
    fireEvent.change(screen.getByLabelText("New watchlist name"), { target: { value: "Leaders" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/watchlists?list=3", { scroll: false }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    fireEvent.change(screen.getByLabelText("List name"), { target: { value: "Core" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("button", { name: "Core" })).toBeInTheDocument();
  });

  it("flips through the list from its first stock", async () => {
    mockLists();
    renderWithClient(<Watchlists />);
    fireEvent.click(await screen.findByRole("button", { name: "Flip through ▸" }));
    expect(push).toHaveBeenCalledWith("/stocks/SPOT");
    expect(useListStore.getState()).toMatchObject({
      source: "Watchlist",
      symbols: ["SPOT", "AAPL"],
    });
  });

  it("explains how to start when there are no lists", async () => {
    mockLists([]);
    renderWithClient(<Watchlists />);
    expect(await screen.findByText(/No watchlists yet/)).toBeInTheDocument();
  });
});
