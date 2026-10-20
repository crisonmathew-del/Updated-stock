/** Shared test fixtures for the Phase 8 verdict, breakout watch and buy list (SPOT near its
 * pivot, the same setup as components/setups/fixtures.ts). */
import { DETAIL, ROW } from "@/components/setups/fixtures";
import type { BuyItem, BuyList, SetupDetail, SetupVerdict, Watch } from "@/lib/api";

const ORDER_TEXT =
  "Place a buy-stop order for 259 shares at 92.56 (limit 97.08), then a stop-loss at 88.71. If the price never reaches 92.56, nothing is bought.";

export const VERDICT: SetupVerdict = {
  action: "get_ready",
  headline: "Get ready",
  reason: "It's 1.0% below its pivot 92.46 and passes every rule: place the order tonight.",
  checks: [
    {
      key: "market",
      label: "Market",
      passed: true,
      detail: "The market is in a confirmed uptrend.",
      short: "",
    },
    {
      key: "fundamentals",
      label: "Fundamentals",
      passed: true,
      detail: "Fundamentals Grade B (needs C or better).",
      short: "",
    },
    {
      key: "earnings",
      label: "Earnings",
      passed: true,
      detail: "Results expected 2025-06-20, 35 sessions away.",
      short: "",
    },
  ],
  order: {
    type: "buy_stop",
    price: 92.56,
    limit: 97.08,
    stop: 88.71,
    shares: 259,
    text: ORDER_TEXT,
  },
  size_pct: 100,
  notes: [],
  passed: 3,
  total: 3,
  plain:
    "Spotify Technology S.A. has been stronger than 91% of stocks over the past year. The app says: Get ready.",
};

export const WATCH: Watch = {
  score: 78,
  on_watch: true,
  parts: [
    {
      key: "distance",
      label: "Close to the pivot",
      points: 23,
      max_points: 25,
      detail: "1.0% below the pivot 92.46.",
    },
    {
      key: "dry_up",
      label: "Volume drying up",
      points: 0,
      max_points: 0,
      detail: "Not enough volume history.",
    },
  ],
  early_entry: {
    entry: 91.9,
    stop: 89.4,
    shares: 400,
    risk_pct: 2.72,
    high: 91.8,
    low: 89.5,
    sessions: 5,
    spread_pct: 2.6,
    text: "Earlier, riskier entry: a buy-stop at 91.90, just above the last 5 days' tight range, with a stop at 89.40.",
  },
};

export const DETAIL_WITH_VERDICT: SetupDetail = {
  ...DETAIL,
  action: "get_ready",
  action_label: "Get ready",
  action_reason: VERDICT.reason,
  watch_score: 78,
  on_watch: true,
  verdict: VERDICT,
  watch: WATCH,
  watch_since: "2025-04-28",
};

export const ORDER_ITEM: BuyItem = {
  ...ROW,
  action: "get_ready",
  action_label: "Get ready",
  action_reason: VERDICT.reason,
  watch_score: 78,
  on_watch: true,
  order: VERDICT.order,
  failed: [],
  plain: VERDICT.plain ?? null,
  watch_parts: WATCH.parts,
  early_entry: WATCH.early_entry,
  watch_since: "2025-04-28",
};

export const WATCH_ITEM: BuyItem = {
  ...ROW,
  id: 6,
  symbol: "CRWD",
  name: "CrowdStrike Holdings, Inc.",
  state: "basing",
  state_label: "Basing",
  readiness_pct: 4.2,
  pivot: 400,
  close: 383.9,
  action: "avoid",
  action_label: "Don't buy",
  action_reason: "It fails 1 of the app's 7 rules: results due in 4 sessions.",
  watch_score: 66,
  on_watch: true,
  order: null,
  failed: ["results due in 4 sessions"],
  plain: null,
  watch_parts: WATCH.parts,
  early_entry: null,
  watch_since: "2025-04-29",
};

export const BUY_LIST_DATA: BuyList = {
  as_of: "2025-04-30",
  market: {
    state: "confirmed_uptrend",
    label: "Confirmed uptrend",
    words: "in a confirmed uptrend",
    allows_buys: true,
    note: "The market is rising: the rules allow new buys.",
  },
  orders: [ORDER_ITEM],
  watch: [ORDER_ITEM, WATCH_ITEM],
  track: {
    picks: 12,
    hits: 4,
    misses: 6,
    pending: 2,
    within_sessions: 10,
    hit_rate_pct: 40,
    median_sessions_to_breakout: 3,
    since: "2025-03-03",
  },
};
