const numberFormat = new Intl.NumberFormat("en-US");

export function formatNumber(value: number): string {
  return numberFormat.format(value);
}

export function formatPercent(part: number, whole: number): string {
  if (whole <= 0) return "0%";
  return `${Math.floor((part / whole) * 100)}%`;
}

export function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Turn "missing_sessions" into "Missing sessions". */
export function humanize(key: string): string {
  const text = key.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Only allow same-site relative redirects after sign-in. */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}

const JOB_LABELS: Record<string, string> = {
  analytics: "Analytics",
  fundamentals: "Fundamentals",
  patterns: "Patterns",
  eod_update: "EOD update",
  data_quality: "Data quality",
  universe: "Universe",
  backfill: "Backfill",
};

export function jobLabel(name: string): string {
  return JOB_LABELS[name] ?? humanize(name);
}

const priceFormat = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatPrice(value: number | null | undefined): string {
  return value == null ? "—" : priceFormat.format(value);
}

/** A fraction (0.125) as a signed percentage ("+12.5%"). */
export function formatChange(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  const pct = value * 100;
  return `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct).toFixed(digits)}%`;
}

/** Rank change with a direction symbol, so it doesn't rely on colour: ▲ 3, ▼ 2, – 0. */
export function formatRankChange(change: number | null): string {
  if (change == null) return "—";
  if (change > 0) return `▲ ${change}`;
  if (change < 0) return `▼ ${Math.abs(change)}`;
  return "– 0";
}

const compactFormat = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** 1234567890 → "1.2B"; null → "—". */
export function formatCompact(value: number | null | undefined): string {
  return value == null ? "—" : compactFormat.format(value);
}

/** A rate between 0 and 1 as a whole percentage ("12%"); null → "—". */
export function formatRate(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

const STATUS_LABELS: Record<string, string> = {
  forming: "Forming",
  broken_out: "Broken out",
  failed: "Failed",
  expired: "No longer valid",
};

export function patternStatus(status: string): string {
  return STATUS_LABELS[status] ?? humanize(status);
}

/** A value already in percent (2.04) with a sign: "+2.0%", "−1.5%"; null → "—". */
export function formatPctPoints(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  const shown = Math.abs(value).toFixed(digits);
  const zero = Number(shown) === 0; // −0.04 shows as "0.0%", not "−0.0%"
  return `${zero ? "" : value > 0 ? "+" : "−"}${shown}%`;
}

/** An R multiple: "+0.05R", "−1.00R"; null → "". */
export function formatR(value: number | null | undefined): string {
  if (value == null) return "";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}R`;
}

/** Distance from the close to the pivot: "2.0% below" (positive readiness) or "1.5% above". */
export function formatReadiness(value: number | null | undefined): string {
  if (value == null) return "—";
  return value >= 0 ? `${value.toFixed(1)}% below` : `${(-value).toFixed(1)}% above`;
}

/** "just now", "4m ago", "2h ago", then the date and time. */
export function formatAgo(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "—";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 12 * 3600) return `${Math.round(seconds / 3600)}h ago`;
  return formatDateTime(iso);
}

const MARKET_ZONE = "America/New_York";

/**
 * A clock time in the viewer's time zone, 24-hour, with the zone's name: "15:15:30 BST"
 * (`seconds: false`: "15:15 BST"; `zone: false` leaves the name off). Browser-only: the
 * server renders in its own zone, so call it for data that arrives after hydration, or behind
 * `useIsClient`.
 */
export function formatClock(
  at: Date | string | number,
  { seconds = true, zone = true }: { seconds?: boolean; zone?: boolean } = {},
): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" as const } : {}),
    hourCycle: "h23",
    timeZoneName: "short",
  }).formatToParts(at instanceof Date ? at : new Date(at));
  const clock = parts
    .filter((p) => p.type === "hour" || p.type === "minute" || p.type === "second")
    .map((p) => p.value)
    .join(":");
  const name = parts.find((p) => p.type === "timeZoneName")?.value;
  return zone && name ? `${clock} ${name}` : clock;
}

/** When a market event happened, in the viewer's time zone: "15:15:30 BST". */
export function formatMarketTime(iso: string | null): string {
  if (!iso) return "—";
  return formatClock(iso);
}

/** Minutes `timeZone` is ahead of UTC at `at` (US/Eastern in summer: −240). */
function zoneOffsetMinutes(timeZone: string, at: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(at);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const wall = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"));
  return Math.round((wall - at.getTime()) / 60_000);
}

/**
 * The instant a US/Eastern wall-clock time ("08:00") falls on: on `weekday` (0 = Sunday) of
 * the current US week if given, else today (US date). The app's schedules are set in US/Eastern;
 * this shows them in the viewer's zone.
 */
export function easternTime(hhmm: string, weekday?: number, now: Date = new Date()): Date {
  const [h, m] = hhmm.split(":").map(Number);
  const [y, mo, d] = now
    .toLocaleDateString("en-CA", { timeZone: MARKET_ZONE })
    .split("-")
    .map(Number);
  const today = new Date(Date.UTC(y, mo - 1, d));
  const shift = weekday === undefined ? 0 : (weekday - today.getUTCDay() + 7) % 7;
  const guess = Date.UTC(y, mo - 1, d + shift, h, m);
  return new Date(guess - zoneOffsetMinutes(MARKET_ZONE, new Date(guess)) * 60_000);
}

/** The viewer's time zone name now: "BST", "GMT". */
export function localZoneName(): string {
  return formatClock(new Date()).split(" ")[1] ?? "";
}

/** A US/Eastern "HH:MM" setting as the viewer's "HH:MM" today ("17:30" → "22:30" in London). */
export function easternToLocalHHMM(hhmm: string): string {
  return formatClock(easternTime(hhmm), { seconds: false, zone: false });
}

/** The viewer's "HH:MM" today as a US/Eastern "HH:MM", the zone the settings are kept in. */
export function localToEasternHHMM(hhmm: string, now: Date = new Date()): string {
  const [h, m] = hhmm.split(":").map(Number);
  const local = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m);
  return local.toLocaleTimeString("en-GB", {
    timeZone: MARKET_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

/** A US/Eastern schedule time in the viewer's zone: "22:30 BST"; with `weekday`, the local
 * day too ("Sunday 23:00 BST"), which can differ from the US day far from New York. */
export function formatEasternSchedule(hhmm: string, weekday?: number): string {
  const at = easternTime(hhmm, weekday);
  const clock = formatClock(at, { seconds: false });
  if (weekday === undefined) return clock;
  return `${at.toLocaleDateString("en-GB", { weekday: "long" })} ${clock}`;
}
