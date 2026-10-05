/**
 * Live position sizing for the trade plan card when the user edits entry or stop. Mirrors the
 * server's rules (api/app/risk/trade_plan.py) and is tested against the same hand-worked
 * numbers: shares = (account × risk%) ÷ (entry − stop), capped so the position is at most
 * max position % of the account; 2R/3R from the risk; reward/risk to the first profit level.
 */
export type SizingSettings = {
  account_size: number;
  risk_per_trade_pct: number;
  max_position_pct: number;
  max_stop_loss_pct: number;
  profit_take_min_pct: number;
  account_currency: string;
};

export type Sizing = {
  riskPerShare: number;
  riskPct: number;
  shares: number;
  cappedByPosition: boolean;
  dollarRisk: number;
  positionValue: number;
  positionPct: number;
  target2r: number;
  target3r: number;
  rewardRisk: number;
  stopTooWide: boolean; // further below the entry than max_stop_loss_pct
};

const cents = (x: number) => Math.round(x * 100) / 100;
const upToCent = (x: number) => Math.ceil(Math.round(x * 100 * 1e6) / 1e6) / 100;

/** Null when the numbers can't make a long trade (stop at or above the entry). */
export function size(entry: number, stop: number, s: SizingSettings): Sizing | null {
  if (!(entry > 0) || !(stop > 0) || stop >= entry) return null;
  const risk = cents(entry - stop);
  if (risk <= 0) return null;
  const byRisk = Math.floor((s.account_size * s.risk_per_trade_pct) / 100 / risk);
  const byPosition = Math.floor((s.account_size * s.max_position_pct) / 100 / entry);
  const shares = Math.max(0, Math.min(byRisk, byPosition));
  const firstProfit = upToCent(entry * (1 + s.profit_take_min_pct / 100));
  return {
    riskPerShare: risk,
    riskPct: cents((risk / entry) * 100),
    shares,
    cappedByPosition: byPosition < byRisk,
    dollarRisk: cents(shares * risk),
    positionValue: cents(shares * entry),
    positionPct: cents(((shares * entry) / s.account_size) * 100),
    target2r: cents(entry + 2 * risk),
    target3r: cents(entry + 3 * risk),
    rewardRisk: cents((firstProfit - entry) / risk),
    stopTooWide: (risk / entry) * 100 > s.max_stop_loss_pct + 1e-9,
  };
}

/** Pick the sizing settings out of GET /api/settings. */
export function sizingSettings(items: { key: string; value: unknown }[]): SizingSettings {
  const get = (key: string) => items.find((i) => i.key === key)?.value;
  return {
    account_size: Number(get("account_size") ?? 100_000),
    risk_per_trade_pct: Number(get("risk_per_trade_pct") ?? 1),
    max_position_pct: Number(get("max_position_pct") ?? 25),
    max_stop_loss_pct: Number(get("max_stop_loss_pct") ?? 8),
    profit_take_min_pct: Number(get("profit_take_min_pct") ?? 20),
    account_currency: String(get("account_currency") ?? "USD"),
  };
}
