import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { LiveQuote } from "@/lib/api";
import { quoteLabel, quoteSession, useLive, useNewerQuote } from "./live";

const quote = (at: string, source?: LiveQuote["source"]): LiveQuote => ({
  symbol: "NVDA",
  last: 103,
  prev_close: 100,
  change_pct: 3,
  open: 101,
  high: 104,
  low: 100.5,
  volume: 1_000_000,
  partial_volume: true,
  at,
  source,
});

beforeEach(() => {
  useLive.setState({ quotes: {} });
});

describe("live quotes", () => {
  it("dates a quote by its US/Eastern session", () => {
    // 00:30 UTC on Oct 3 is still Oct 2 in New York (after-hours).
    expect(quoteSession(quote("2026-10-03T00:30:00Z"))).toBe("2026-10-02");
    expect(quoteSession(undefined)).toBeNull();
  });

  it("says whether a price is streamed or from the 15-minute check", () => {
    expect(quoteLabel(quote("2026-10-05T14:15:30Z", "stream"))).toBe("Live 10:15:30 ET");
    expect(quoteLabel(quote("2026-10-05T14:45:00Z", "check"))).toBe("Checked 10:45:00 ET");
  });

  it("uses a quote only when it's newer than the stored close", () => {
    const { result } = renderHook(() => useNewerQuote("nvda", "2026-10-02"));
    expect(result.current).toBeUndefined();
    act(() => useLive.setState({ quotes: { NVDA: quote("2026-10-02T19:59:00Z") } }));
    expect(result.current).toBeUndefined(); // the processed session: the close is the price
    act(() => useLive.setState({ quotes: { NVDA: quote("2026-10-05T14:15:30Z") } }));
    expect(result.current?.last).toBe(103);
  });
});
