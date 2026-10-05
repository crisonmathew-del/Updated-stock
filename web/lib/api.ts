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
