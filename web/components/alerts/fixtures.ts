import type { Alert, AlertRule, AlertsPage } from "@/lib/api";

export function alert(id: number, extra: Partial<Alert> = {}): Alert {
  return {
    id,
    created_at: "2026-10-02T14:15:31Z",
    session_date: "2026-10-02",
    kind: "breakout_provisional",
    kind_label: "Breakout (provisional)",
    priority: "high",
    symbol: "SPOT",
    title: "SPOT breaking out strongly (provisional)",
    body: "92.65 is above the 92.46 pivot on projected volume of 298% of average (strong).",
    payload: {},
    delivery: { in_app: "sent", email: "sent" },
    read: false,
    ...extra,
  };
}

export function page(items: Alert[], extra: Partial<AlertsPage> = {}): AlertsPage {
  return {
    items,
    unread: items.filter((a) => !a.read).length,
    kinds: [{ kind: "breakout_provisional", label: "Breakout (provisional)", count: items.length }],
    next_before: null,
    ...extra,
  };
}

export function rule(id: number, extra: Partial<AlertRule> = {}): AlertRule {
  return {
    id,
    name: "SPOT over 95",
    enabled: true,
    scope: "ticker",
    symbol: "SPOT",
    watchlist_id: null,
    watchlist_name: null,
    screen_id: null,
    screen_name: null,
    condition: "price_above",
    value: 95,
    ma: null,
    channels: ["email", "in_app"],
    priority: "high",
    description: "When SPOT trades above 95.00: email and in-app, high priority.",
    last_fired_at: null,
    ...extra,
  };
}
