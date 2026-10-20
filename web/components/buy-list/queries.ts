/** The buy list's query, shared by the page (server prefetch) and its panels (client). */
export const BUY_LIST = { key: ["buy-list"], path: "/api/buy-list" } as const;

/** Five minutes: it changes once a day, after the evening scan (and live data refetches it). */
export const BUY_LIST_STALE_MS = 5 * 60_000;
