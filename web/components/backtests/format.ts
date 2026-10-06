import type { BacktestProgress, BacktestRun } from "@/lib/api";
import { formatNumber } from "@/lib/format";

export const money = (x: number | null | undefined) =>
  x == null ? "—" : `${x < 0 ? "−" : ""}$${formatNumber(Math.round(Math.abs(x)))}`;

/** Rounded the way `Change` rounds (5.55 → "5.6"), with a real minus sign. */
function fixed(x: number, digits: number): string {
  const shown = Math.abs(x).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: false,
  });
  return `${x < 0 && Number(shown) !== 0 ? "−" : ""}${shown}`;
}

export const num = (x: number | null | undefined, digits = 2) =>
  x == null ? "—" : fixed(x, digits);

export const pct = (x: number | null | undefined, digits = 1) =>
  x == null ? "—" : `${fixed(x, digits)}%`;

const REGIMES: Record<string, string> = {
  confirmed_uptrend: "Confirmed uptrend",
  uptrend_under_pressure: "Uptrend under pressure",
  correction: "Correction",
};

export function regimeLabel(key: string): string {
  return REGIMES[key] ?? (key === "unknown" ? "No regime yet" : key);
}

/** What a run is doing, in words. */
export function progressText(run: Pick<BacktestRun, "status" | "progress" | "error">): string {
  const p: BacktestProgress = run.progress ?? {};
  if (run.status === "failed") return run.error ? `Failed: ${run.error}` : "Failed.";
  if (run.status === "done") return "Done.";
  if (run.status === "queued") return "Waiting for the worker…";
  if (p.stage === "tape") {
    const share = p.total ? Math.floor(((p.done ?? 0) / p.total) * 100) : 0;
    return `Replaying the scan: ${formatNumber(p.done ?? 0)} of ${formatNumber(p.total ?? 0)} stocks (${share}%)`;
  }
  if (p.stage === "simulate") return "Simulating the portfolio…";
  return "Starting…";
}
