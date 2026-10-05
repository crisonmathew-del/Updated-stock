import type { ScreenerSnapshot, ScreenFilter } from "@/lib/api";
import { formatCompact, formatPctPoints, formatPrice, formatReadiness } from "@/lib/format";
import { patternLabel, STAGES } from "@/lib/stages";

/**
 * The screener's client side (spec §8.4, §10): the snapshot arrives once as columns, is
 * decoded into rows, and every filter, sort and export runs in the browser.
 */

export type Row = {
  symbol: string;
  name: string;
  type: string;
  sector: string | null;
  group: string | null;
  group_rank: number | null;
  close: number | null;
  change_pct: number | null;
  volume: number | null;
  volume_ratio: number | null;
  dollar_volume: number | null;
  market_cap: number | null;
  rs_rating: number | null;
  rs_line_high: boolean;
  rs_ahead: boolean;
  stage: number | null;
  tt_passed: number | null;
  tt_pass: boolean;
  off_high_pct: number | null;
  above_low_pct: number | null;
  vs_sma50_pct: number | null;
  fund_grade: string | null;
  setup_state: string | null;
  setup_kind: string | null;
  pattern: string | null;
  grade: string | null;
  score: number | null;
  readiness_pct: number | null;
  pivot: number | null;
  breakout_today: boolean;
  pocket_pivot_today: boolean;
  earnings_gap_recent: boolean;
  liquid: boolean;
  spark: string;
};

export type Field = keyof Row;

/** Columnar snapshot → one object per stock. */
export function decode(snapshot: ScreenerSnapshot): Row[] {
  const index = snapshot.fields.map((f, i) => [f, i] as const);
  return snapshot.rows.map((values) => {
    const row: Record<string, unknown> = {};
    for (const [field, i] of index) row[field] = values[i];
    return row as Row;
  });
}

export type Kind = "text" | "enum" | "bool" | "number" | "price" | "pct" | "spark";

export type FieldGroup =
  "Stock" | "Price & volume" | "Trend" | "Relative strength" | "Setup" | "Fundamentals" | "Events";

export type FieldDef = {
  label: string;
  /** Column header when the label is too long for it. */
  short?: string;
  description: string;
  kind: Kind;
  group: FieldGroup;
  width: number;
  /** Fixed choices for enum fields; otherwise they come from the data. */
  options?: { value: string; label: string }[];
  /** The sort key when it isn't the raw value (grades best-first, stages in lifecycle order). */
  sortValue?: (row: Row) => number | string | null;
  /** Largest first on the first click (numbers), or smallest (names, distances). */
  descFirst?: boolean;
  format?: (value: unknown, row: Row) => string;
  /** Unit appended to numbers in filter chips ("%", "×"). */
  unit?: string;
};

const opts = (values: string[], label: (v: string) => string = (v) => v) =>
  values.map((value) => ({ value, label: label(value) }));

const GRADE_RANK: Record<string, number> = { "A+": 5, A: 4, B: 3, C: 2, D: 1, E: 0 };
const STATE_ORDER = [
  "near_pivot",
  "breakout",
  "basing",
  "watch",
  "extended",
  "failed",
  "invalidated",
];

const num = (digits: number) => (v: unknown) =>
  v == null ? "—" : (v as number).toLocaleString("en-US", { maximumFractionDigits: digits });

export const FIELDS: Record<Field, FieldDef> = {
  symbol: {
    label: "Symbol",
    description: "Ticker and company name.",
    kind: "text",
    group: "Stock",
    width: 150,
    descFirst: false,
  },
  name: { label: "Name", description: "Company name.", kind: "text", group: "Stock", width: 200 },
  type: {
    label: "Type",
    description: "Common stock or ADR.",
    kind: "enum",
    group: "Stock",
    width: 72,
    options: [
      { value: "common", label: "Common" },
      { value: "adr", label: "ADR" },
    ],
  },
  sector: {
    label: "Sector",
    description: "SEC-based sector.",
    kind: "enum",
    group: "Stock",
    width: 150,
  },
  group: {
    label: "Industry group",
    short: "Group",
    description: "Industry group (from SEC SIC codes).",
    kind: "enum",
    group: "Stock",
    width: 180,
  },
  group_rank: {
    label: "Group rank",
    short: "Grp #",
    description: "The industry group's rank (1 = strongest).",
    kind: "number",
    group: "Relative strength",
    width: 52,
    descFirst: false,
  },
  close: {
    label: "Price",
    description: "Last close.",
    kind: "price",
    group: "Price & volume",
    width: 72,
  },
  change_pct: {
    label: "Change %",
    short: "Chg",
    description: "Change on the day, in %.",
    kind: "pct",
    group: "Price & volume",
    width: 72,
    unit: "%",
  },
  volume: {
    label: "Volume",
    description: "Shares traded on the day.",
    kind: "number",
    group: "Price & volume",
    width: 76,
    format: (v) => formatCompact(v as number | null),
  },
  volume_ratio: {
    label: "Volume vs average",
    short: "Vol ×",
    description: "The day's volume as a multiple of the 50-day average.",
    kind: "number",
    group: "Price & volume",
    width: 56,
    format: (v) => (v == null ? "—" : `${(v as number).toFixed(1)}×`),
    unit: "×",
  },
  dollar_volume: {
    label: "Average $ volume",
    short: "$ vol",
    description: "Average dollar volume over 50 days.",
    kind: "number",
    group: "Price & volume",
    width: 72,
    format: (v) => (v == null ? "—" : `$${formatCompact(v as number)}`),
  },
  market_cap: {
    label: "Market cap",
    short: "Mkt cap",
    description: "Market capitalisation (left empty for ADRs).",
    kind: "number",
    group: "Price & volume",
    width: 72,
    format: (v) => (v == null ? "—" : `$${formatCompact(v as number)}`),
  },
  rs_rating: {
    label: "RS Rating",
    short: "RS",
    description: "Relative strength rank vs all stocks, 1-99.",
    kind: "number",
    group: "Relative strength",
    width: 44,
  },
  rs_line_high: {
    label: "RS line at 52-week high",
    short: "RS hi",
    description: "The RS line made a 52-week high today.",
    kind: "bool",
    group: "Relative strength",
    width: 60,
  },
  rs_ahead: {
    label: "RS leads price",
    short: "RS lead",
    description: "The RS line is at a new high before the price is.",
    kind: "bool",
    group: "Relative strength",
    width: 64,
  },
  stage: {
    label: "Stage",
    description: "Weinstein stage (2 = advancing).",
    kind: "number",
    group: "Trend",
    width: 48,
    descFirst: false,
  },
  tt_passed: {
    label: "Trend Template checks",
    short: "TT",
    description: "How many of the 8 Trend Template checks pass.",
    kind: "number",
    group: "Trend",
    width: 44,
    format: (v) => (v == null ? "—" : `${v as number}/8`),
  },
  tt_pass: {
    label: "Trend Template",
    short: "TT ✓",
    description: "All 8 Trend Template checks pass.",
    kind: "bool",
    group: "Trend",
    width: 56,
  },
  off_high_pct: {
    label: "From 52-week high",
    short: "Off high",
    description: "Distance below the 52-week high, in %.",
    kind: "pct",
    group: "Trend",
    width: 68,
    unit: "%",
  },
  above_low_pct: {
    label: "Above 52-week low",
    short: "Off low",
    description: "Distance above the 52-week low, in %.",
    kind: "pct",
    group: "Trend",
    width: 80,
    unit: "%",
  },
  vs_sma50_pct: {
    label: "Vs 50-day",
    short: "vs 50d",
    description: "Distance from the 50-day average, in %.",
    kind: "pct",
    group: "Trend",
    width: 72,
    unit: "%",
  },
  fund_grade: {
    label: "Fundamentals Grade",
    short: "Fund",
    description: "Fundamentals Grade A-E from SEC filings.",
    kind: "enum",
    group: "Fundamentals",
    width: 44,
    options: opts(["A", "B", "C", "D", "E"]),
    sortValue: (r) => (r.fund_grade ? (GRADE_RANK[r.fund_grade] ?? null) : null),
  },
  setup_state: {
    label: "Setup stage",
    short: "Stage now",
    description: "Where the active setup is in its lifecycle.",
    kind: "enum",
    group: "Setup",
    width: 100,
    options: STATE_ORDER.map((s) => ({ value: s, label: STAGES[s as keyof typeof STAGES].label })),
    sortValue: (r) => (r.setup_state ? STATE_ORDER.indexOf(r.setup_state) : null),
    descFirst: false,
  },
  setup_kind: {
    label: "Setup kind",
    description: "Base, episodic pivot or watch.",
    kind: "enum",
    group: "Setup",
    width: 96,
    options: [
      { value: "base", label: "Base" },
      { value: "episodic_pivot", label: "Episodic pivot" },
      { value: "watch", label: "Watch" },
    ],
  },
  pattern: {
    label: "Pattern",
    description: "The active setup's pattern.",
    kind: "enum",
    group: "Setup",
    width: 104,
    options: opts(
      [
        "vcp",
        "cup_with_handle",
        "flat_base",
        "ascending_base",
        "high_tight_flag",
        "three_weeks_tight",
        "earnings_gap",
      ],
      patternLabel,
    ),
    format: (v) => patternLabel(v as string | null),
  },
  grade: {
    label: "Setup grade",
    short: "Setup",
    description: "Setup grade and score (A+ ≥ 90 by default).",
    kind: "enum",
    group: "Setup",
    width: 68,
    options: opts(["A+", "A", "B", "C"]),
    sortValue: (r) => r.score,
  },
  score: {
    label: "Setup Score",
    short: "Score",
    description: "Setup Score, 0-100.",
    kind: "number",
    group: "Setup",
    width: 60,
    format: num(1),
  },
  readiness_pct: {
    label: "Distance to pivot",
    short: "To pivot",
    description: "% the close is below the pivot (negative: above it).",
    kind: "pct",
    group: "Setup",
    width: 100,
    descFirst: false,
    format: (v) => formatReadiness(v as number | null),
    unit: "%",
  },
  pivot: {
    label: "Pivot",
    description: "The setup's buy point.",
    kind: "price",
    group: "Setup",
    width: 76,
  },
  breakout_today: {
    label: "Breakout today",
    short: "Brk",
    description: "Broke out at today's close.",
    kind: "bool",
    group: "Events",
    width: 48,
  },
  pocket_pivot_today: {
    label: "Pocket pivot today",
    short: "PP",
    description: "A pocket pivot today.",
    kind: "bool",
    group: "Events",
    width: 48,
  },
  earnings_gap_recent: {
    label: "Earnings gap-up (5 sessions)",
    short: "Gap",
    description: "An earnings gap-up in the last 5 sessions that still holds.",
    kind: "bool",
    group: "Events",
    width: 48,
  },
  liquid: {
    label: "Passes universe filter",
    short: "Univ",
    description: "Price, liquidity and market-cap minimums from the settings.",
    kind: "bool",
    group: "Stock",
    width: 52,
  },
  spark: {
    label: "6-month trend",
    short: "6M",
    description: "The last 120 closes.",
    kind: "spark",
    group: "Price & volume",
    width: 68,
  },
};

export const FIELD_GROUPS: FieldGroup[] = [
  "Stock",
  "Price & volume",
  "Trend",
  "Relative strength",
  "Setup",
  "Fundamentals",
  "Events",
];

export const DEFAULT_COLUMNS: Field[] = [
  "symbol",
  "spark",
  "close",
  "change_pct",
  "volume_ratio",
  "rs_rating",
  "tt_passed",
  "group_rank",
  "fund_grade",
  "pattern",
  "setup_state",
  "grade",
  "readiness_pct",
  "off_high_pct",
  "market_cap",
];

/** A cell's text (also what CSV export writes, minus units). */
export function display(field: Field, row: Row): string {
  const def = FIELDS[field];
  const value = row[field];
  if (def.format) return def.format(value, row);
  if (value == null || value === "") return "—";
  switch (def.kind) {
    case "price":
      return formatPrice(value as number);
    case "pct":
      return formatPctPoints(value as number);
    case "bool":
      return value ? "✓" : "";
    case "enum":
      return def.options?.find((o) => o.value === value)?.label ?? String(value);
    default:
      return String(value);
  }
}

export function sortValue(field: Field, row: Row): number | string | null {
  const def = FIELDS[field];
  if (def.sortValue) return def.sortValue(row);
  const value = row[field];
  if (typeof value === "boolean") return value ? 1 : 0;
  return (value as number | string | null) ?? null;
}

// --- Filters ---------------------------------------------------------------------------------

export function matches(row: Row, filters: ScreenFilter[]): boolean {
  for (const f of filters) {
    const value = row[f.field as Field];
    if (f.op === "is") {
      if (Boolean(value) !== f.value) return false;
    } else if (f.op === "in") {
      if (f.values.length > 0 && !f.values.includes(value as string)) return false;
    } else {
      if (f.min == null && f.max == null) continue;
      if (typeof value !== "number") return false;
      if (f.min != null && value < f.min) return false;
      if (f.max != null && value > f.max) return false;
    }
  }
  return true;
}

export function applyFilters(rows: Row[], filters: ScreenFilter[]): Row[] {
  return filters.length ? rows.filter((r) => matches(r, filters)) : rows;
}

/** A new filter for a field with nothing chosen yet (a yes/no starts at "yes"). */
export function newFilter(field: Field): ScreenFilter {
  const kind = FIELDS[field].kind;
  if (kind === "bool") return { field, op: "is", value: true };
  if (kind === "enum" || kind === "text") return { field, op: "in", values: [] };
  return { field, op: "between", min: null, max: null };
}

const trim = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

/** "RS ≥ 85", "Price 10–50", "Pattern: VCP, Flat base", "Trend Template: yes". */
export function describeFilter(f: ScreenFilter): string {
  const def = FIELDS[f.field as Field];
  const label = def?.label ?? f.field;
  if (f.op === "is") return `${label}: ${f.value ? "yes" : "no"}`;
  if (f.op === "in") {
    if (f.values.length === 0) return `${label}: any`;
    const names = f.values.map((v) => def?.options?.find((o) => o.value === v)?.label ?? v);
    return `${label}: ${names.length > 3 ? `${names.slice(0, 3).join(", ")} +${names.length - 3}` : names.join(", ")}`;
  }
  const unit = def?.unit ?? "";
  if (f.min != null && f.max != null) return `${label} ${trim(f.min)}–${trim(f.max)}${unit}`;
  if (f.min != null) return `${label} ≥ ${trim(f.min)}${unit}`;
  if (f.max != null) return `${label} ≤ ${trim(f.max)}${unit}`;
  return `${label}: any`;
}

// --- Presets ---------------------------------------------------------------------------------

export type Sort = { field: Field; desc: boolean };

export type Screen = {
  name: string;
  description?: string;
  filters: ScreenFilter[];
  sort: Sort | null;
  columns: Field[];
};

export type Preset = Screen & { id: string };

const LIQUID: ScreenFilter = { field: "liquid", op: "is", value: true };

export const PRESETS: Preset[] = [
  {
    id: "trend-template-leaders",
    name: "Trend Template leaders",
    description: "All 8 Trend Template checks pass. Strongest RS first.",
    filters: [LIQUID, { field: "tt_pass", op: "is", value: true }],
    sort: { field: "rs_rating", desc: true },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "vcps-near-pivot",
    name: "VCPs near pivot",
    description: "Volatility contraction patterns within reach of their pivot. Closest first.",
    filters: [
      LIQUID,
      { field: "pattern", op: "in", values: ["vcp"] },
      { field: "setup_state", op: "in", values: ["near_pivot"] },
    ],
    sort: { field: "readiness_pct", desc: false },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "breakouts-today",
    name: "Breakouts today",
    description: "Closed above the pivot on heavy volume today.",
    filters: [LIQUID, { field: "breakout_today", op: "is", value: true }],
    sort: { field: "grade", desc: true },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "pocket-pivots-today",
    name: "Pocket pivots today",
    description: "An up day on more volume than any recent down day, often inside a base.",
    filters: [LIQUID, { field: "pocket_pivot_today", op: "is", value: true }],
    sort: { field: "rs_rating", desc: true },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "earnings-gap-ups",
    name: "Earnings gap-ups",
    description: "Gapped up on earnings in the last 5 sessions and still holding the gap.",
    filters: [LIQUID, { field: "earnings_gap_recent", op: "is", value: true }],
    sort: { field: "change_pct", desc: true },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "rs-leads-price",
    name: "RS leads price",
    description: "The RS line is at a new high before the price is: a sign of hidden strength.",
    filters: [LIQUID, { field: "rs_ahead", op: "is", value: true }],
    sort: { field: "rs_rating", desc: true },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "high-tight-flags",
    name: "High tight flags",
    description: "A sharp advance, then a short, shallow flag. Rare and powerful.",
    filters: [LIQUID, { field: "pattern", op: "in", values: ["high_tight_flag"] }],
    sort: { field: "grade", desc: true },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "top-group-leaders",
    name: "Top group leaders",
    description: "RS 85+ in the 20 strongest industry groups.",
    filters: [
      LIQUID,
      { field: "group_rank", op: "between", min: 1, max: 20 },
      { field: "rs_rating", op: "between", min: 85, max: null },
    ],
    sort: { field: "group_rank", desc: false },
    columns: DEFAULT_COLUMNS,
  },
  {
    id: "fundamentals-a-rs-90",
    name: "Fundamentals A + RS ≥ 90",
    description: "Top fundamentals and top relative strength.",
    filters: [
      LIQUID,
      { field: "fund_grade", op: "in", values: ["A"] },
      { field: "rs_rating", op: "between", min: 90, max: null },
    ],
    sort: { field: "rs_rating", desc: true },
    columns: DEFAULT_COLUMNS,
  },
];

export const ALL_STOCKS: Preset = {
  id: "all",
  name: "All stocks",
  description: "Every stock that passes the universe filter. Add filters to build a screen.",
  filters: [LIQUID],
  sort: { field: "rs_rating", desc: true },
  columns: DEFAULT_COLUMNS,
};

/** Same filters, sort and columns (what "unsaved changes" compares). */
export function sameScreen(a: Screen, b: Screen): boolean {
  const key = (s: Screen) => JSON.stringify([s.filters, s.sort, s.columns]);
  return key(a) === key(b);
}

// --- Sparklines and CSV ----------------------------------------------------------------------

/** Base64 bytes (0-255) → SVG polyline points in a width × height box (y down). */
export function sparkPoints(encoded: string, width: number, height: number): string {
  if (!encoded) return "";
  const bytes = atob(encoded);
  if (bytes.length < 2) return "";
  const step = width / (bytes.length - 1);
  const parts: string[] = [];
  for (let i = 0; i < bytes.length; i++) {
    const y = height - 1 - (bytes.charCodeAt(i) / 255) * (height - 2);
    parts.push(`${(i * step).toFixed(1)},${y.toFixed(1)}`);
  }
  return parts.join(" ");
}

function csvCell(value: unknown): string {
  if (value == null) return "";
  const text = typeof value === "boolean" ? (value ? "yes" : "no") : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Raw values (not formatted) of the chosen columns; the symbol column also writes the name. */
export function toCsv(rows: Row[], columns: Field[]): string {
  const fields = columns.filter((c) => FIELDS[c].kind !== "spark");
  const out = fields.flatMap((f) => (f === "symbol" ? ["symbol", "name"] : [f]));
  const lines = [out.map(csvCell).join(",")];
  for (const row of rows) lines.push(out.map((f) => csvCell(row[f as Field])).join(","));
  return lines.join("\r\n") + "\r\n";
}
