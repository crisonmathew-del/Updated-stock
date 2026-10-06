/** Query keys and paths for the backtest lab. */
export const BACKTESTS = { key: ["backtests"], path: "/api/backtests" } as const;
export const OPTIONS = { key: ["backtests", "options"], path: "/api/backtests/options" } as const;

export function runQuery(id: number) {
  return { key: ["backtests", id], path: `/api/backtests/${id}` } as const;
}

export function tradeChartPath(id: number, n: number): string {
  return `/api/backtests/${id}/trades/${n}/chart`;
}

/** Poll while a run is waiting or working; stop once everything has finished. */
export function pollWhileActive<T extends { status: string }>(runs: T[] | T | undefined) {
  const list = runs == null ? [] : Array.isArray(runs) ? runs : [runs];
  return list.some((r) => r.status === "queued" || r.status === "running") ? 2000 : false;
}
