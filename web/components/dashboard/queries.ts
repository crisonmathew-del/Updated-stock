/**
 * The dashboard's queries, shared by its panels (client) and the page (server prefetch), so
 * the keys the server fills are exactly the ones the panels read.
 */
export const DASHBOARD = {
  regime: { key: ["market", "regime", 60], path: "/api/market/regime?days=60" },
  breadth: { key: ["market", "breadth", 20], path: "/api/market/breadth?days=20" },
  topSetups: {
    key: ["setups", "dashboard", "top"],
    path: "/api/setups?state=basing,near_pivot,breakout&sort=score&limit=10",
  },
  nearPivot: {
    key: ["setups", "dashboard", "near"],
    path: "/api/setups?state=near_pivot&sort=readiness&limit=10",
  },
  breakouts: {
    key: ["signals", "dashboard", "breakouts"],
    path: "/api/signals?type=breakout&limit=10",
  },
  signals: { key: ["signals", "dashboard", "feed"], path: "/api/signals?limit=12" },
  groups: { key: ["groups", 10], path: "/api/groups?limit=10" },
} as const;

/** Five minutes: the data changes once a day, after the evening scan. */
export const DASHBOARD_STALE_MS = 5 * 60_000;
