import type { Holding, LiveQuote } from "@/lib/api";

// Entry 100.00, initial stop 92.00 (8.00 a share), stop raised to 96.00, 50 shares.
export const HOLDING: Holding = {
  id: 1,
  symbol: "SPOT",
  name: "Spotify",
  setup_id: null,
  opened_on: "2026-09-15",
  entry_price: 100,
  shares: 50,
  initial_stop: 92,
  stop: 96,
  note: null,
  closed_on: null,
  exit_price: null,
  price: 112,
  price_at: "2026-10-01",
  price_source: "close",
  day_change_pct: 1.82,
  pnl: 600,
  pnl_pct: 12,
  r: 1.5,
  risk_per_share: 8,
  position_value: 5600,
  open_risk: 800,
  warnings: [],
};

export const QUOTE: LiveQuote = {
  symbol: "SPOT",
  last: 104.2,
  prev_close: 112,
  change_pct: -6.96,
  open: 111,
  high: 112.5,
  low: 103.9,
  volume: 2_000_000,
  partial_volume: true,
  at: "2026-10-02T15:31:00Z",
};
