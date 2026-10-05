import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SavedScreen, ScreenerSnapshot } from "@/lib/api";
import { FIELDS, type Row } from "@/lib/screener";
import { mockApi, renderWithClient } from "@/test-utils";
import { activeFrom, Screener } from "./screener";

const replace = vi.fn();
const push = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push, prefetch: vi.fn() }),
  usePathname: () => "/screener",
  useSearchParams: () => new URLSearchParams(search),
}));
// The chart needs a canvas; the preview's other contents are what these tests check.
vi.mock("@/components/stock/chart-panel", () => ({
  ChartPanel: ({ symbol }: { symbol: string }) => <div>Chart of {symbol}</div>,
}));

beforeEach(() => {
  search = "";
  // jsdom lays nothing out; the virtualised table measures its scroller's offset size.
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(720);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(1200);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  replace.mockReset();
  push.mockReset();
});

const FIELD_NAMES = Object.keys(FIELDS) as (keyof Row)[];

function row(values: Partial<Row>): unknown[] {
  const full: Record<string, unknown> = {
    ...Object.fromEntries(FIELD_NAMES.map((f) => [f, null])),
    liquid: true,
    tt_pass: false,
    spark: "",
    ...values,
  };
  return FIELD_NAMES.map((f) => full[f]);
}

const SNAPSHOT: ScreenerSnapshot = {
  as_of: "2026-10-02",
  fields: FIELD_NAMES,
  groups: 0,
  rows: [
    row({
      symbol: "SPOT",
      name: "Spotify Technology S.A.",
      close: 94,
      change_pct: 1.35,
      rs_rating: 96,
      tt_pass: true,
      tt_passed: 8,
      pattern: "vcp",
      setup_state: "near_pivot",
      grade: "A",
      score: 84.2,
      readiness_pct: 1.04,
    }),
    row({
      symbol: "AAPL",
      name: "Apple Inc.",
      close: 230.5,
      rs_rating: 88,
      tt_pass: true,
      tt_passed: 8,
    }),
    row({ symbol: "SLOW", name: "Slowco", close: 12.5, rs_rating: 40, tt_passed: 3 }),
    row({ symbol: "THIN", name: "Thin Trading", close: 4, rs_rating: 99, liquid: false }),
  ],
};

const SAVED: SavedScreen = {
  id: 7,
  name: "Leaders RS 90",
  filters: [
    { field: "liquid", op: "is", value: true },
    { field: "rs_rating", op: "between", min: 90, max: null },
  ],
  sort: { field: "rs_rating", desc: true },
  columns: ["symbol", "close", "rs_rating"],
  updated_at: "2026-10-03T12:00:00Z",
};

function mockScreener(saved: SavedScreen[] = []) {
  return mockApi({
    "GET /api/screener": { body: SNAPSHOT },
    "GET /api/screens": { body: saved },
    "POST /api/screens": { status: 201, body: { ...SAVED, id: 8, name: "My leaders" } },
    "PATCH /api/screens/7": { body: SAVED },
    "POST /api/watchlists/default/items": {
      body: { id: 1, name: "Watchlist", position: 0, items: [] },
    },
  });
}

const symbols = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => r.id.replace("screener-row-", ""));

describe("activeFrom", () => {
  it("reads a saved screen or a preset from the URL, else the first preset", () => {
    expect(activeFrom(new URLSearchParams("screen=7"))).toEqual({ kind: "saved", id: 7 });
    expect(activeFrom(new URLSearchParams("preset=high-tight-flags"))).toEqual({
      kind: "preset",
      id: "high-tight-flags",
    });
    expect(activeFrom(new URLSearchParams("preset=nope"))).toEqual({
      kind: "preset",
      id: "trend-template-leaders",
    });
  });
});

describe("Screener", () => {
  it("opens on the first preset with its matches, sorted, and counts for every preset", async () => {
    mockScreener();
    renderWithClient(<Screener />);

    await screen.findByRole("row", { name: /SPOT/ });
    expect(screen.getByRole("heading", { name: "Trend Template leaders" })).toBeInTheDocument();
    expect(screen.getByText(/of 4 stocks/)).toHaveTextContent(/^2 of 4 stocks · as of Oct 2 close/);
    expect(symbols()).toEqual(["SPOT", "AAPL"]); // RS Rating, highest first
    const presets = screen.getByRole("navigation", { name: "Screens" });
    expect(within(presets).getByRole("button", { name: /VCPs near pivot/ })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: "Remove filter Trend Template: yes" })).toBeVisible();

    fireEvent.click(within(presets).getByRole("button", { name: /VCPs near pivot/ }));
    expect(replace).toHaveBeenCalledWith("/screener?preset=vcps-near-pivot", { scroll: false });
  });

  it("re-sorts from the column headers, missing values last", async () => {
    search = "preset=all";
    mockScreener();
    renderWithClient(<Screener />);
    await screen.findByRole("row", { name: /SPOT/ });
    expect(symbols()).toEqual(["SPOT", "AAPL", "SLOW"]);

    fireEvent.click(screen.getByRole("button", { name: /^Price/ }));
    expect(screen.getByRole("columnheader", { name: /Price/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    expect(symbols()).toEqual(["AAPL", "SPOT", "SLOW"]);
    fireEvent.click(screen.getByRole("button", { name: /^Price/ }));
    expect(symbols()).toEqual(["SLOW", "SPOT", "AAPL"]);
    expect(screen.getByRole("heading", { name: /All stocks/ })).toHaveTextContent("edited");
  });

  it("builds a filter from any field", async () => {
    search = "preset=all";
    mockScreener();
    renderWithClient(<Screener />);
    await screen.findByRole("row", { name: /SPOT/ });

    fireEvent.click(screen.getByRole("button", { name: "＋ Add filter" }));
    fireEvent.change(await screen.findByPlaceholderText("Find a field…"), {
      target: { value: "rs rat" },
    });
    fireEvent.click(screen.getByRole("button", { name: "RS Rating" }));
    fireEvent.change(await screen.findByLabelText(/Min/), { target: { value: "90" } });

    await waitFor(() => expect(symbols()).toEqual(["SPOT"]));
    expect(screen.getByRole("button", { name: "RS Rating ≥ 90" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove filter RS Rating ≥ 90" }));
    await waitFor(() => expect(symbols()).toHaveLength(3));
  });

  it("previews the clicked row and moves with j / k, w and Esc", async () => {
    const fetchMock = mockScreener();
    renderWithClient(<Screener />);
    const spot = await screen.findByRole("row", { name: /SPOT/ });

    fireEvent.click(spot);
    const preview = screen.getByRole("complementary", { name: "SPOT preview" });
    expect(within(preview).getByText("Chart of SPOT")).toBeInTheDocument();
    expect(within(preview).getByText("1.0% below")).toBeInTheDocument();
    expect(spot).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(window, { key: "j" });
    expect(screen.getByRole("complementary", { name: "AAPL preview" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k" });
    expect(screen.getByRole("complementary", { name: "SPOT preview" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "w" });
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/watchlists/default/items",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ symbol: "SPOT" }) }),
      ),
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("adds a row to the watchlist from its ＋ button", async () => {
    const fetchMock = mockScreener();
    renderWithClient(<Screener />);
    fireEvent.click(await screen.findByRole("button", { name: "Add AAPL to watchlist" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/watchlists/default/items",
        expect.objectContaining({ body: JSON.stringify({ symbol: "AAPL" }) }),
      ),
    );
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("saves the current filters as a new screen", async () => {
    const fetchMock = mockScreener();
    renderWithClient(<Screener />);
    await screen.findByRole("heading", { name: "Trend Template leaders" });

    fireEvent.click(screen.getByRole("button", { name: "Save as new screen" }));
    fireEvent.change(screen.getByLabelText("Screen name"), { target: { value: "My leaders" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/screener?screen=8", { scroll: false }),
    );
    const call = fetchMock.mock.calls.find(
      ([url, init]) => url === "/api/screens" && init?.method === "POST",
    );
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body).toMatchObject({
      name: "My leaders",
      filters: [
        { field: "liquid", op: "is", value: true },
        { field: "tt_pass", op: "is", value: true },
      ],
      sort: { field: "rs_rating", desc: true },
    });
  });

  it("opens a saved screen from the URL and saves changes to it", async () => {
    search = "screen=7";
    const fetchMock = mockScreener([SAVED]);
    renderWithClient(<Screener />);

    expect(await screen.findByRole("heading", { name: "Leaders RS 90" })).toBeInTheDocument();
    await waitFor(() => expect(symbols()).toEqual(["SPOT"]));
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Watch",
      "Symbol",
      "Price",
      "RS▼",
    ]);
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Price/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/screens/7",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
  });
});
