/**
 * The stock page's queries, shared by its components (client) and the page (server prefetch),
 * so the keys the server fills are exactly the ones the components read.
 */
export const stockQueries = (symbol: string) => ({
  summary: { key: ["stock", symbol, "summary"], path: `/api/stocks/${symbol}` },
  setup: { key: ["stock", symbol, "setup"], path: `/api/stocks/${symbol}/setup` },
  fundamentals: {
    key: ["stock", symbol, "fundamentals"],
    path: `/api/stocks/${symbol}/fundamentals`,
  },
  peers: { key: ["stock", symbol, "peers"], path: `/api/stocks/${symbol}/peers` },
  note: { key: ["stock", symbol, "note"], path: `/api/stocks/${symbol}/note` },
  membership: { key: ["membership", symbol], path: `/api/stocks/${symbol}/watchlists` },
});

export function chartQuery(symbol: string, timeframe: string, sessions: number) {
  return {
    key: ["stock", symbol, "chart", timeframe, sessions],
    path: `/api/stocks/${symbol}/chart?timeframe=${timeframe}&sessions=${sessions}`,
  };
}

export const SETTINGS_QUERY = { key: ["settings"], path: "/api/settings" } as const;

/** Chart ranges on keys 1-5 (spec §8.1). Intraday arrives with real-time data in Phase 6. */
export const RANGES = [
  { key: "1", label: "6M", timeframe: "daily", sessions: 504, visible: 126 },
  { key: "2", label: "1Y", timeframe: "daily", sessions: 504, visible: 252 },
  { key: "3", label: "2Y", timeframe: "daily", sessions: 504, visible: 504 },
  { key: "4", label: "2Y W", timeframe: "weekly", sessions: 1260, visible: 104 },
  { key: "5", label: "5Y W", timeframe: "weekly", sessions: 1260, visible: 260 },
] as const;

/** 1Y daily: what a first visit shows. */
export const DEFAULT_RANGE = RANGES[1];
