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
