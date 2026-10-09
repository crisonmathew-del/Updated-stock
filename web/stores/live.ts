import { create } from "zustand";
import type { LiveQuote, ScanResult, SetupEvent } from "@/lib/api";
import { formatMarketTime } from "@/lib/format";

/** What the live socket has told this page: the latest quote per stock (today's session only),
 * this session's setup events (provisional breakouts, extensions, stops) and the scans. */
type LiveState = {
  connected: boolean;
  quotes: Record<string, LiveQuote>;
  events: SetupEvent[];
  scans: { premarket: ScanResult | null; sweep: ScanResult | null };
  setConnected: (connected: boolean) => void;
  applyQuotes: (quotes: LiveQuote[]) => void;
  addEvent: (event: SetupEvent) => void;
  /** The session's events so far (from GET /api/live), keeping any newer ones already here. */
  setEvents: (events: SetupEvent[]) => void;
  setScan: (scan: "premarket" | "sweep", result: ScanResult | null) => void;
  /** Forget the session's quotes, events and scans (its close has been processed). */
  clearSession: () => void;
};

const MAX_EVENTS = 200;

export const useLive = create<LiveState>((set) => ({
  connected: false,
  quotes: {},
  events: [],
  scans: { premarket: null, sweep: null },
  setConnected: (connected) => set({ connected }),
  applyQuotes: (quotes) =>
    set((s) => {
      const next = { ...s.quotes };
      for (const q of quotes) next[q.symbol] = q;
      return { quotes: next };
    }),
  addEvent: (event) => set((s) => ({ events: [event, ...s.events].slice(0, MAX_EVENTS) })),
  setEvents: (events) =>
    set((s) => {
      const key = (e: SetupEvent) => `${e.kind}:${e.symbol}:${e.at}`;
      const known = new Set(events.map(key));
      const newer = s.events.filter((e) => !known.has(key(e)));
      return { events: [...newer, ...events].slice(0, MAX_EVENTS) };
    }),
  setScan: (scan, result) => set((s) => ({ scans: { ...s.scans, [scan]: result } })),
  clearSession: () => set({ quotes: {}, events: [], scans: { premarket: null, sweep: null } }),
}));

/** The live quote for a stock, if the streamer has one from today. */
export function useLiveQuote(symbol: string | null | undefined): LiveQuote | undefined {
  return useLive((s) => (symbol ? s.quotes[symbol.toUpperCase()] : undefined));
}

/** The session (US/Eastern date, "YYYY-MM-DD") a quote is from. */
export function quoteSession(quote: LiveQuote | undefined): string | null {
  return quote?.at
    ? new Date(quote.at).toLocaleDateString("en-CA", { timeZone: "America/New_York" })
    : null;
}

/** The live quote for a stock when it's newer than the stored close `closeDate` (a later
 * session); otherwise the close is the price and this is undefined. */
export function useNewerQuote(
  symbol: string | null | undefined,
  closeDate: string | null | undefined,
): LiveQuote | undefined {
  const quote = useLiveQuote(symbol);
  const day = quoteSession(quote);
  return day && (!closeDate || day > closeDate) ? quote : undefined;
}

/** "Live 10:45:12 ET" for a streamed price, "Checked 10:45:00 ET" for a sweep's. */
export function quoteLabel(quote: LiveQuote): string {
  return `${quote.source === "check" ? "Checked" : "Live"} ${formatMarketTime(quote.at)}`;
}

/** This session's latest setup event for a stock (newest first in the store). */
export function useSetupEvent(symbol: string): SetupEvent | undefined {
  return useLive((s) => s.events.find((e) => e.symbol === symbol));
}
