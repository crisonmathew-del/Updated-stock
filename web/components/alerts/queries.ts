/** Query keys and paths for alerts, shared by the bell, the alerts centre and the live socket. */
export const ALERTS = ["alerts"] as const;
export const UNREAD = { key: ["alerts", "unread"], path: "/api/alerts/unread" } as const;
export const RECENT = { key: ["alerts", "list", "recent"], path: "/api/alerts?limit=8" } as const;
export const STATUS = { key: ["alerts", "status"], path: "/api/alerts/status" } as const;
export const RULES = { key: ["alert-rules"], path: "/api/alert-rules" } as const;
export const HOLDINGS = ["holdings"] as const;
export const LIVE = { key: ["live"], path: "/api/live" } as const;

export type AlertFilters = {
  kind?: string;
  priority?: string;
  symbol?: string;
  unread?: boolean;
};

export function alertsPath(filters: AlertFilters, before?: number | null, limit = 50): string {
  const params = new URLSearchParams();
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.priority) params.set("priority", filters.priority);
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.unread) params.set("unread", "true");
  if (before != null) params.set("before", String(before));
  params.set("limit", String(limit));
  return `/api/alerts?${params.toString()}`;
}
