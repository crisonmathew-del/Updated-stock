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
