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
