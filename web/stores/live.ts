import { create } from "zustand";
import type { LiveQuote, ScanResult, SetupEvent } from "@/lib/api";

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
  setScan: (scan: "premarket" | "sweep", result: ScanResult | null) => void;
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
  setScan: (scan, result) => set((s) => ({ scans: { ...s.scans, [scan]: result } })),
}));

/** The live quote for a stock, if the streamer has one from today. */
export function useLiveQuote(symbol: string | null | undefined): LiveQuote | undefined {
  return useLive((s) => (symbol ? s.quotes[symbol.toUpperCase()] : undefined));
}

/** This session's latest setup event for a stock (newest first in the store). */
export function useSetupEvent(symbol: string): SetupEvent | undefined {
  return useLive((s) => s.events.find((e) => e.symbol === symbol));
}
