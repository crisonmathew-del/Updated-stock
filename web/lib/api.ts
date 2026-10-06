// Typed client for the Breakout API. All requests go to this origin's /api/*, which Next.js
// proxies to the FastAPI service (see next.config.ts). Writes carry the CSRF header the API
// requires; a 401 anywhere except the login call sends the user to /login.

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const CSRF_HEADERS = { "X-Requested-With": "breakout" } as const;

type Detail = string | { key?: string; message?: string; msg?: string; loc?: unknown[] }[];

function messageFrom(detail: Detail | undefined, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d) => {
        const field = d.key ?? (Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "");
        const text = d.message ?? d.msg ?? "is invalid";
        return field ? `${field}: ${text}` : text;
      })
      .join("; ");
  }
  return `The API returned ${status}.`;
}

function redirectToLogin() {
  if (typeof window === "undefined" || window.location.pathname === "/login") return;
  const next = window.location.pathname + window.location.search;
  // A full page load on purpose: it also drops every cached query from the expired session.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") Object.assign(headers, CSRF_HEADERS);
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError("Can't reach the API. Check that the api service is running.", null);
  }

  if (response.status === 401 && path !== "/api/auth/login") redirectToLogin();
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: Detail };
    throw new ApiError(messageFrom(payload.detail, response.status), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => send<T>("GET", path),
  post: <T>(path: string, body?: unknown) => send<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => send<T>("PATCH", path, body),
  put: <T>(path: string, body: unknown) => send<T>("PUT", path, body),
  delete: <T = void>(path: string) => send<T>("DELETE", path),
};

// --- Health (public) ----------------------------------------------------------------------

export type ComponentStatus = {
  ok: boolean;
  detail: string | null;
  latency_ms: number | null;
  last_seen: string | null;
};

export type Readiness = {
  status: "ok" | "degraded";
  checked_at: string;
  components: Record<string, ComponentStatus>;
};

/** Readiness returns 503 with a full body when something is down, so both 200 and 503 parse. */
export async function getReadiness(): Promise<Readiness> {
  let response: Response;
  try {
    response = await fetch("/api/health/ready", { cache: "no-store" });
  } catch {
    throw new ApiError("Can't reach the API. Check that the api service is running.", null);
  }
  if (response.status !== 200 && response.status !== 503) {
    throw new ApiError(
      `The API returned ${response.status}. Is the api service healthy?`,
      response.status,
    );
  }
  return (await response.json()) as Readiness;
}

// --- Admin ---------------------------------------------------------------------------------

export type JobRun = {
  id: number;
  job_name: string;
  trigger: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
  stats: Record<string, unknown>;
};

export type UniverseSummary = {
  active: number;
  inactive: number;
  stocks: number;
  benchmarks: number;
  by_type: Record<string, number>;
  by_exchange: Record<string, number>;
  with_cik: number;
  with_sic: number;
  with_market_cap: number;
  backfill: { done: number; pending: number; failed: number; no_data: number };
  earliest_bar: string | null;
  latest_bar: string | null;
  last_build: JobRun | null;
};

export type BackfillProgress = {
  status: "idle" | "running" | "succeeded" | "failed";
  run_id: number | null;
  total: number;
  processed: number;
  done: number;
  no_data: number;
  failed: number;
  bars_written: number;
  start: string | null;
  end: string | null;
  started_at: string | null;
  updated_at: string | null;
  message: string | null;
  current: string[];
};

export type Severity = "critical" | "warning" | "info";

export type DataIssue = {
  id: number;
  check: string;
  severity: Severity;
  symbol: string | null;
  issue_date: string | null;
  detail: string;
  first_detected_at: string;
  last_detected_at: string;
};

export type DataHealth = {
  summary: Record<Severity, number>;
  by_check: Record<string, Partial<Record<Severity, number>>>;
  issues: DataIssue[];
  last_check: JobRun | null;
};

export type Enqueued = { job: string; job_id: string };

// --- Market and stocks ------------------------------------------------------------------------

export type IndexRegime = {
  symbol: string;
  state: string;
  label: string;
  close: number | null;
  ema21: number | null;
  sma50: number | null;
  sma200: number | null;
  change_pct: number | null;
  distribution_days: number;
  distribution_dates: string[];
  rally_day: number | null;
  is_ftd: boolean;
  last_ftd_date: string | null;
  reasons: string[];
};

export type Regime = {
  date: string | null;
  state: string | null;
  label: string | null;
  changed_from: string | null;
  reasons: string[];
  indexes: IndexRegime[];
  history: {
    date: string;
    states: Record<string, string>;
    distribution_days: Record<string, number>;
    is_ftd: boolean;
  }[];
};

export type BreadthDay = {
  date: string;
  members: number;
  pct_above_50: number | null;
  pct_above_200: number | null;
  new_highs: number;
  new_lows: number;
  net_new_highs: number;
  advancers: number;
  decliners: number;
  ad_line: number;
};

export type GroupRow = {
  group_id: number;
  rank: number;
  rank_change_4w: number | null;
  name: string;
  sector: string;
  members: number;
  median_rs: number | null;
  return_3m: number | null;
  return_6m: number | null;
  tt_passing: number;
  new_highs: number;
};

export type SectorRow = {
  symbol: string;
  sector: string;
  rank: number;
  rank_change_4w: number | null;
  rs_raw: number | null;
  return_3m: number | null;
};

export type Groups = { date: string | null; groups: GroupRow[]; sectors: SectorRow[] };

export type TrendCheck = { key: string; label: string; passed: boolean; detail: string };

export type StockSummary = {
  symbol: string;
  name: string;
  exchange: string;
  type: string;
  sector: string | null;
  industry: string | null;
  market_cap: number | null;
  date: string | null;
  close: number | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
  volume_ratio: number | null;
  high_52w: number | null;
  low_52w: number | null;
  next_earnings: string | null;
  sessions_to_earnings: number | null;
  fundamentals_grade: string | null;
  stage: number | null;
  stage_label: string | null;
  rs_rating: number | null;
  trend_template_passed: number;
  trend_template_pass: boolean;
  checks: TrendCheck[];
  group: {
    id: number;
    name: string;
    sector: string;
    rank: number | null;
    rank_change_4w: number | null;
    ranked_groups: number | null;
  } | null;
  indicators: Record<string, number | boolean | null>;
};

// --- Fundamentals (Phase 3) ---------------------------------------------------------------

export type GradeComponent = {
  key: string;
  label: string;
  points: number;
  max_points: number;
  status: "pass" | "partial" | "fail" | "no_data";
  detail: string;
  bonus: boolean;
};

export type Grade = {
  date: string;
  grade: string | null;
  score: number | null;
  path: "eps" | "revenue";
  basis: "quarterly" | "annual" | "none";
  coverage_pct: number;
  components: GradeComponent[];
};

export type FiscalPeriod = {
  period_end: string;
  label: string;
  reported_date: string;
  eps: number | null;
  revenue: number | null;
  net_income: number | null;
  eps_growth_pct: number | null;
  eps_note: "turnaround" | "loss" | null;
  revenue_growth_pct: number | null;
  derived: boolean;
  currency: string | null;
};

export type EarningsDate = {
  report_date: string;
  status: "reported" | "estimated";
  timing: string;
};

export type InsiderTrade = {
  transaction_date: string;
  filed_date: string;
  insider_name: string;
  role: string;
  code: "P" | "S";
  shares: number;
  price: number | null;
};

export type Fundamentals = {
  symbol: string;
  as_of: string | null;
  refreshed_at: string | null;
  grade: Grade | null;
  quarters: FiscalPeriod[];
  years: FiscalPeriod[];
  earnings: EarningsDate[];
  insiders: InsiderTrade[];
};

// --- Patterns (Phase 3) -------------------------------------------------------------------

export type PatternComponent = {
  key: string;
  label: string;
  points: number;
  max_points: number;
  detail: string;
};

export type PatternPoint = { date: string; price: number; kind: "high" | "low" };

export type Verdict = "correct" | "wrong" | "unsure";

export type PatternReview = { verdict: Verdict; note: string | null; reviewed_at: string };

export type Pattern = {
  id: number;
  symbol: string;
  name: string;
  type: string;
  type_label: string;
  timeframe: "daily" | "weekly";
  start_date: string;
  end_date: string;
  pivot: number;
  base_low: number | null;
  depth_pct: number | null;
  duration_weeks: number;
  quality: number;
  base_number: number | null;
  status: "forming" | "broken_out" | "failed" | "expired";
  status_date: string;
  first_detected: string;
  last_seen: string;
  components: PatternComponent[];
  swings: PatternPoint[];
  contractions: { high: PatternPoint; low: PatternPoint; depth_pct: number }[];
  details: Record<string, unknown>;
  review: PatternReview | null;
};

export type PatternTypeStats = {
  type: string;
  type_label: string;
  detected: number;
  reviewed: number;
  correct: number;
  wrong: number;
  unsure: number;
  false_positive_rate: number | null;
};

export type ReviewStats = {
  types: PatternTypeStats[];
  reviewed: number;
  false_positive_rate: number | null;
};

// --- Setups and signals (Phase 4) -------------------------------------------------------------

export type SetupState =
  "watch" | "basing" | "near_pivot" | "breakout" | "extended" | "failed" | "invalidated";

export type ScoreComponent = {
  key: string;
  label: string;
  points: number;
  max_points: number;
  status: "pass" | "partial" | "fail" | "no_data";
  detail: string;
};

export type RedFlag = { key: string; label: string; penalty: number; detail: string };

export type TradePlan = {
  entry: number;
  stop: number;
  stop_basis: "logical" | "max_loss";
  logical_stop: number;
  max_loss_stop: number;
  risk_too_wide: boolean;
  risk_per_share: number;
  risk_pct: number;
  shares: number;
  capped_by_position_limit: boolean;
  dollar_risk: number;
  position_value: number;
  position_pct: number;
  buy_zone: [number, number];
  target_2r: number;
  target_3r: number;
  profit_take: [number, number];
  breakeven_at: number;
  breakeven_basis: "2R" | "gain";
  trail_aggressive: number | null;
  trail_standard: number | null;
  reward_risk: number;
  currency: string;
  notes: string[];
};

export type SetupRow = {
  id: number;
  symbol: string;
  name: string;
  kind: "watch" | "base" | "episodic_pivot";
  pattern_type: string | null;
  pattern_label: string | null;
  state: SetupState;
  state_label: string;
  state_since: string;
  first_seen: string;
  as_of: string;
  active: boolean;
  close: number;
  pivot: number | null;
  base_low: number | null;
  readiness_pct: number | null;
  score: number;
  raw_score: number;
  grade: string | null;
  best_grade: string | null;
  breakout_date: string | null;
  entry: number | null;
  stop: number | null;
  shares: number | null;
  risk_too_wide: boolean | null;
  red_flags: string[];
  closed_on: string | null;
  closed_reason: string | null;
};

export type SetupTransition = {
  date: string;
  from_state: string | null;
  to_state: string;
  to_label: string;
  reason: string;
};

export type SignalOutcome = {
  sessions_observed: number;
  returns: Record<string, number | null>;
  returns_r: Record<string, number | null>;
  mfe_pct: number | null;
  mae_pct: number | null;
  stop_hit_on: string | null;
  target_2r_on: string | null;
  gain_20_on: string | null;
  complete: boolean;
};

export type SignalEntry = {
  id: number;
  date: string;
  type: string;
  type_label: string;
  symbol: string | null;
  name: string | null;
  setup_id: number | null;
  summary: string;
  price: number | null;
  pivot: number | null;
  entry: number | null;
  stop: number | null;
  score: number | null;
  grade: string | null;
  context: Record<string, unknown>;
  outcome: SignalOutcome | null;
  setup_state: SetupState | null;
};

export type SetupDetail = SetupRow & {
  regime_multiplier: number;
  penalties: number;
  components: ScoreComponent[];
  red_flag_details: RedFlag[];
  trade_plan: TradePlan | null;
  transitions: SetupTransition[];
  signals: SignalEntry[];
  pattern: Pattern | null;
};

export type SetupList = {
  as_of: string | null;
  total: number;
  counts: Partial<Record<SetupState, number>>;
  items: SetupRow[];
};

export type SignalList = {
  total: number;
  counts: Record<string, number>;
  items: SignalEntry[];
};

// --- Phase 5: search, chart, screener, watchlists ------------------------------------------

export type SearchHit = {
  symbol: string;
  name: string;
  exchange: string;
  type: string;
  date: string | null;
  close: number | null;
  change_pct: number | null;
  grade: string | null;
  score: number | null;
  state: SetupState | null;
};

export type Quote = {
  symbol: string;
  date: string | null;
  close: number | null;
  change_pct: number | null;
};

export type ChartPoint = { time: string; price: number; kind: "high" | "low" };

export type ChartData = {
  symbol: string;
  timeframe: "daily" | "weekly";
  series: {
    time: string[];
    open: number[];
    high: number[];
    low: number[];
    close: number[];
    volume: number[];
    avg_volume: (number | null)[];
    ma: Record<string, (number | null)[]>;
    rs_line: (number | null)[];
  };
  markers: {
    time: string;
    kind: "pocket_pivot" | "earnings" | "gap" | "rs_high" | "signal";
    label: string;
    text: string;
  }[];
  overlay: {
    pattern_id: number;
    type: string;
    label: string;
    status: string;
    start: string;
    end: string;
    pivot: number;
    base_low: number | null;
    buy_zone_top: number;
    swings: ChartPoint[];
    contractions: { number: number; high: ChartPoint; low: ChartPoint; depth_pct: number }[];
    setup_state: SetupState | null;
    entry: number | null;
    stop: number | null;
    target_2r: number | null;
    target_3r: number | null;
  } | null;
};

export type Peer = {
  symbol: string;
  name: string;
  close: number | null;
  change_pct: number | null;
  rs_rating: number | null;
  stage: number | null;
  grade: string | null;
  score: number | null;
  state: SetupState | null;
  is_self: boolean;
};

export type Note = { body: string; updated_at: string | null };

export type Membership = { id: number; name: string; contains: boolean };

export type WatchlistItem = {
  symbol: string;
  name: string;
  position: number;
  note: string | null;
  added_at: string;
  date: string | null;
  close: number | null;
  change_pct: number | null;
  rs_rating: number | null;
  grade: string | null;
  score: number | null;
  state: SetupState | null;
  pivot: number | null;
  readiness_pct: number | null;
};

export type Watchlist = { id: number; name: string; position: number; items: WatchlistItem[] };

export type ScreenerSnapshot = {
  as_of: string | null;
  fields: string[];
  rows: unknown[][];
  groups: number;
};

export type ScreenFilter =
  | { field: string; op: "between"; min?: number | null; max?: number | null }
  | { field: string; op: "is"; value: boolean }
  | { field: string; op: "in"; values: string[] };

export type SavedScreen = {
  id: number;
  name: string;
  filters: ScreenFilter[];
  sort: { field: string; desc: boolean } | null;
  columns: string[] | null;
  updated_at: string;
};

// --- Alerts, holdings and live data (Phase 6) -----------------------------------------------

export type AlertPriority = "high" | "normal";

export type Alert = {
  id: number;
  created_at: string;
  session_date: string;
  kind: string;
  kind_label: string;
  priority: AlertPriority;
  symbol: string | null;
  title: string;
  body: string;
  payload: Record<string, unknown>;
  /** What each channel did, in words: "sent", "digest", "held: quiet hours", "failed: …". */
  delivery: { in_app?: string; email?: string };
  read: boolean;
};

export type AlertsPage = {
  items: Alert[];
  unread: number;
  kinds: { kind: string; label: string; count: number }[];
  next_before: number | null;
};

export type AlertsStatus = {
  email: { configured: boolean; provider: string | null; detail: string };
  streamer: {
    alive: boolean;
    state: string;
    provider: string | null;
    detail: string | null;
    since: string | null;
  };
  quiet_hours_now: boolean;
};

export type RuleScope = "ticker" | "watchlist" | "holdings" | "screen";
export type RuleCondition =
  | "price_above"
  | "price_below"
  | "ma_cross_above"
  | "ma_cross_below"
  | "change_above"
  | "change_below"
  | "volume_ratio_above"
  | "new_match";
export type MovingAverage = "ema10" | "ema21" | "sma50" | "sma150" | "sma200";
export type AlertChannel = "in_app" | "email";

export type AlertRule = {
  id: number;
  name: string;
  enabled: boolean;
  scope: RuleScope;
  symbol: string | null;
  watchlist_id: number | null;
  watchlist_name: string | null;
  screen_id: number | null;
  screen_name: string | null;
  condition: RuleCondition;
  value: number | null;
  ma: MovingAverage | null;
  channels: AlertChannel[];
  priority: AlertPriority;
  description: string;
  last_fired_at: string | null;
};

export type AlertRuleInput = {
  name: string;
  enabled?: boolean;
  scope: RuleScope;
  symbol?: string | null;
  watchlist_id?: number | null;
  screen_id?: number | null;
  condition: RuleCondition;
  value?: number | null;
  ma?: MovingAverage | null;
  channels: AlertChannel[];
  priority: AlertPriority;
};

export type HoldingWarning = { rule: string; priority: AlertPriority; title: string; body: string };

export type Holding = {
  id: number;
  symbol: string;
  name: string;
  setup_id: number | null;
  opened_on: string;
  entry_price: number;
  shares: number;
  initial_stop: number;
  stop: number;
  note: string | null;
  closed_on: string | null;
  exit_price: number | null;
  price: number | null;
  price_at: string | null;
  price_source: "live" | "close" | "exit" | null;
  day_change_pct: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  r: number | null;
  risk_per_share: number;
  position_value: number | null;
  open_risk: number | null;
  warnings: HoldingWarning[];
};

export type HoldingInput = {
  symbol: string;
  entry_price: number;
  shares: number;
  initial_stop: number;
  stop?: number | null;
  opened_on?: string | null;
  note?: string | null;
  setup_id?: number | null;
};

export type LiveQuote = {
  symbol: string;
  last: number;
  prev_close: number | null;
  change_pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number;
  partial_volume: boolean;
  at: string | null;
};

export type ScanItem = {
  scan: "premarket" | "sweep";
  symbol: string;
  name: string;
  at: string;
  price: number;
  prev_close: number;
  change_pct: number;
  volume: number;
  volume_pct: number;
  earnings: boolean;
  grade: string | null;
  setup_state: string | null;
};

export type ScanResult = { at: string; items: ScanItem[] };

export type LiveSnapshot = {
  session: string;
  quotes: Record<string, LiveQuote>;
  events: SetupEvent[];
  premarket: ScanResult | null;
  sweep: ScanResult | null;
};

export type SetupEvent = {
  kind: "breakout_provisional" | "breakout_extended" | "setup_stop";
  symbol: string;
  setup_id: number | null;
  price: number;
  at: string;
  title: string;
};

export type IntradayBars = {
  symbol: string;
  date: string | null;
  interval: number;
  prev_close: number | null;
  open_time: number | null;
  time: number[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: number[];
  sessions: string[];
};

/** What the live socket sends (`/api/ws`). */
export type LiveEvent =
  | { type: "alert"; data: Alert }
  | { type: "quotes"; data: LiveQuote[] }
  | { type: "setup_event"; data: SetupEvent }
  | { type: "scan"; scan: "premarket" | "sweep"; data: ScanResult };

export type LiveMessage =
  { type: "hello"; unread: number } | { type: "batch"; events: LiveEvent[] } | { type: "pong" };

// --- Backtest lab (Phase 7) ------------------------------------------------------------------

export type SetupGrade = "A+" | "A" | "B" | "C";

export type BacktestRules = {
  min_grade: SetupGrade | null;
  patterns: string[];
  near_pivot_only: boolean;
  skip_risk_too_wide: boolean;
  skip_correction: boolean;
  screen_id: number | null;
  screen_name: string | null;
  screen_filters: ScreenFilter[];
};

export type BacktestPortfolio = {
  initial_capital: number;
  risk_pct: number;
  max_position_pct: number;
  max_positions: number;
  slippage_pct: number;
  commission: number;
};

export type BacktestExits = {
  sell_unconfirmed: boolean;
  trailing: "sma50" | "ema21" | "none";
  time_stop_sessions: number;
  time_stop_min_gain_pct: number;
  partial_profit_pct: number;
  partial_fraction_pct: number;
  breakeven_r: number;
  breakeven_gain_pct: number;
};

export type BacktestParams = {
  start: string;
  end: string;
  rules: BacktestRules;
  portfolio: BacktestPortfolio;
  exits: BacktestExits;
  buy_zone_pct: number;
  in_sample_pct: number;
  sensitivity: boolean;
};

export type BacktestOptions = {
  defaults: BacktestParams;
  first_date: string | null;
  last_date: string | null;
  patterns: { value: string; label: string }[];
  screens: { id: number; name: string }[];
  grid: { vcp: number[]; volume: number[] };
  running: number | null;
};

export type BacktestInput = {
  name?: string | null;
  start: string;
  end: string;
  rules: Omit<BacktestRules, "screen_id" | "screen_name" | "screen_filters">;
  portfolio: BacktestPortfolio;
  exits: BacktestExits;
  sensitivity: boolean;
  screen_id: number | null;
};

export type BacktestHeadline = {
  total_return_pct: number | null;
  cagr_pct: number | null;
  max_drawdown_pct: number | null;
  sharpe: number | null;
  trades: number;
  win_rate_pct: number | null;
  expectancy_r: number | null;
  profit_factor: number | null;
  benchmark_cagr_pct: number | null;
};

export type BacktestProgress = { stage?: string; done?: number; total?: number };

export type BacktestRun = {
  id: number;
  name: string;
  status: "queued" | "running" | "done" | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  start: string;
  end: string;
  sensitivity: boolean;
  screen: string | null;
  summary: BacktestHeadline | null;
  progress: BacktestProgress;
  error: string | null;
};

export type BacktestMetrics = BacktestHeadline & {
  start?: string | null;
  end?: string | null;
  start_equity: number | null;
  end_equity: number | null;
  max_drawdown_peak: string | null;
  max_drawdown_trough: string | null;
  sortino: number | null;
  volatility_pct: number | null;
  exposure_pct: number | null;
  wins: number;
  losses: number;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  payoff_ratio: number | null;
  avg_win_r: number | null;
  avg_loss_r: number | null;
  net_profit: number | null;
  avg_sessions: number | null;
  best_pct: number | null;
  worst_pct: number | null;
  stopped_pct: number | null;
  partial_pct: number | null;
  benchmark_total_return_pct?: number | null;
  benchmark_max_drawdown_pct?: number | null;
};

export type BreakdownRow = {
  key: string;
  trades: number;
  win_rate_pct: number | null;
  expectancy_r: number | null;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  profit_factor: number | null;
  net_profit: number | null;
};

export type HeatmapCell = {
  cagr_pct: number | null;
  max_drawdown_pct: number | null;
  trades: number;
  win_rate_pct: number | null;
  expectancy_r: number | null;
  profit_factor: number | null;
};

export type BacktestReport = {
  hypothetical: boolean;
  labels: { hypothetical: string; survivorship: string };
  assumptions: string[];
  period: { start: string | null; end: string | null; sessions: number; split: string | null };
  summary: BacktestMetrics;
  equity: {
    dates: string[];
    equity: number[];
    benchmark: (number | null)[];
    drawdown: number[];
    positions: number[];
    exposure: number[];
  };
  samples: { split_pct: number; in: BacktestMetrics; out: BacktestMetrics };
  by_regime: BreakdownRow[];
  by_pattern: BreakdownRow[];
  by_grade: BreakdownRow[];
  by_exit: BreakdownRow[];
  by_year: BreakdownRow[];
  orders: Record<string, number>;
  signals: Record<string, number>;
  tape: Record<string, number | boolean>;
  heatmap: {
    vcp: number[];
    volume: number[];
    base: { vcp: number; volume: number };
    cells: (HeatmapCell | null)[][];
  } | null;
};

export type BacktestFill = { date: string; price: number; shares: number; reason: string };

export type BacktestTrade = {
  n: number;
  ticker_id: number;
  symbol: string;
  setup: number;
  pattern: string | null;
  grade: string | null;
  score: number | null;
  regime: string | null;
  signal_date: string;
  entry_date: string;
  entry_price: number;
  stop: number;
  shares: number;
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  partial: boolean;
  exits: BacktestFill[];
  pnl: number;
  pnl_pct: number;
  r: number;
  sessions: number;
  sample: "in" | "out";
};

export type BacktestDetail = BacktestRun & {
  params: BacktestParams;
  report: BacktestReport | null;
  trades: BacktestTrade[];
};

export type TradeChart = {
  symbol: string;
  time: string[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  sma50: (number | null)[];
  trade: BacktestTrade;
};

// --- Signal performance (Phase 7) ------------------------------------------------------------

export type PerformanceStats = {
  signals: number;
  measured: number;
  win_rate_pct: number | null;
  avg_return_pct: number | null;
  avg_gain_pct: number | null;
  avg_loss_pct: number | null;
  expectancy_r: number | null;
  r_count: number;
  stop_hit_pct: number | null;
  reached_20_pct: number | null;
  median_days_to_20: number | null;
};

export type PerformanceType = {
  type: string;
  label: string;
  r: boolean;
  all: PerformanceStats;
  buckets: (PerformanceStats & { bucket: string })[];
  regimes: (PerformanceStats & { regime: string })[];
};

export type Performance = {
  horizon: number;
  since: string | null;
  first: string | null;
  last: string | null;
  total: PerformanceStats;
  types: PerformanceType[];
};

// --- AI summary (Phase 7) --------------------------------------------------------------------

export type AiSummary = {
  thesis: string;
  catalyst: string;
  risks: string[];
  unverified: string[];
  model: string;
  generated_at: string;
  as_of: string | null;
  cached: boolean;
};

export type AiSummaryState = { enabled: boolean; model: string; summary: AiSummary | null };
