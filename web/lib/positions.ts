import type { Holding, LiveQuote } from "@/lib/api";

/**
 * A holding re-priced with a live quote that is newer than its price (the API priced it at the
 * last close or an earlier quote). Mirrors the API's arithmetic (api/app/api/routes/holdings.py):
 * P&L = (price − entry) × shares; R = (price − entry) ÷ (entry − initial stop); open risk =
 * what's lost if the current stop is hit.
 */
export function livePosition(h: Holding, quote: LiveQuote | undefined): Holding {
  if (!quote || h.closed_on || quote.at == null) return h;
  if (h.price_source === "live" && h.price_at && h.price_at >= quote.at) return h;
  const price = quote.last;
  const risk = h.entry_price - h.initial_stop;
  const round = (x: number, places = 2) => Math.round(x * 10 ** places) / 10 ** places;
  return {
    ...h,
    price,
    price_at: quote.at,
    price_source: "live",
    day_change_pct: quote.change_pct,
    pnl: round((price - h.entry_price) * h.shares),
    pnl_pct: round((price / h.entry_price - 1) * 100),
    r: risk > 0 ? round((price - h.entry_price) / risk) : null,
    position_value: round(price * h.shares),
    open_risk: round(Math.max(price - h.stop, 0) * h.shares),
  };
}

export type Totals = { value: number; pnl: number; openRisk: number; count: number };

export function totals(rows: Holding[]): Totals {
  return rows.reduce<Totals>(
    (t, h) => ({
      value: t.value + (h.position_value ?? 0),
      pnl: t.pnl + (h.pnl ?? 0),
      openRisk: t.openRisk + (h.open_risk ?? 0),
      count: t.count + 1,
    }),
    { value: 0, pnl: 0, openRisk: 0, count: 0 },
  );
}
