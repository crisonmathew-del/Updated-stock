"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Change } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { api, type BacktestRun } from "@/lib/api";
import { formatR } from "@/lib/format";
import { num, pct, progressText } from "./format";
import { BACKTESTS, OPTIONS, pollWhileActive } from "./queries";

const STATUS: Record<BacktestRun["status"], { icon: string; tone: string; label: string }> = {
  queued: { icon: "…", tone: "text-muted", label: "Queued" },
  running: { icon: "◌", tone: "text-tide-ink", label: "Running" },
  done: { icon: "✓", tone: "text-rise", label: "Done" },
  failed: { icon: "✕", tone: "text-fall", label: "Failed" },
};

export function StatusLabel({ run }: { run: BacktestRun }) {
  const s = STATUS[run.status];
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap ${s.tone}`}>
      <span aria-hidden>{s.icon}</span>
      {s.label}
    </span>
  );
}

export function RunList() {
  const client = useQueryClient();
  const runs = useQuery({
    queryKey: BACKTESTS.key,
    queryFn: () => api.get<BacktestRun[]>(BACKTESTS.path),
    refetchInterval: (q) => pollWhileActive(q.state.data),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/api/backtests/${id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: BACKTESTS.key });
      void client.invalidateQueries({ queryKey: OPTIONS.key });
    },
  });
  if (runs.isPending) return <div className="h-24 animate-pulse rounded bg-surface-2" />;
  if (runs.error)
    return (
      <p role="alert" className="text-sm text-fall">
        {runs.error.message}
      </p>
    );
  if (!runs.data.length)
    return (
      <p className="text-sm text-muted">
        No backtests yet. Set the rules below and press “Run backtest”.
      </p>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Backtest runs, newest first</caption>
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1 pr-3 font-normal">Run</th>
            <th className="py-1 pr-3 font-normal">Status</th>
            <th className="py-1 pr-3 text-right font-normal">CAGR</th>
            <th className="py-1 pr-3 text-right font-normal">SPY</th>
            <th className="py-1 pr-3 text-right font-normal">Max drawdown</th>
            <th className="py-1 pr-3 text-right font-normal">Trades</th>
            <th className="py-1 pr-3 text-right font-normal">Win rate</th>
            <th className="py-1 pr-3 text-right font-normal">Expectancy</th>
            <th className="py-1 pr-3 text-right font-normal">Profit factor</th>
            <th className="py-1 font-normal">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.data.map((run) => {
            const s = run.summary;
            return (
              <tr key={run.id} className="border-t border-border align-top">
                <td className="py-2 pr-3">
                  <Link href={`/backtests/${run.id}`} className="hover:underline">
                    {run.name}
                  </Link>
                  <div className="text-xs text-muted">
                    {run.start} to {run.end}
                    {run.sensitivity ? " · with heatmap" : ""}
                  </div>
                </td>
                <td className="py-2 pr-3">
                  <StatusLabel run={run} />
                  {run.status !== "done" && (
                    <div className="max-w-xs text-xs text-muted">{progressText(run)}</div>
                  )}
                </td>
                <td className="tabular py-2 pr-3 text-right">
                  <Change value={s?.cagr_pct} digits={1} />
                </td>
                <td className="tabular py-2 pr-3 text-right text-muted">
                  {pct(s?.benchmark_cagr_pct)}
                </td>
                <td className="tabular py-2 pr-3 text-right">{pct(s?.max_drawdown_pct)}</td>
                <td className="tabular py-2 pr-3 text-right">{s?.trades ?? "—"}</td>
                <td className="tabular py-2 pr-3 text-right">{pct(s?.win_rate_pct, 0)}</td>
                <td className="tabular py-2 pr-3 text-right">
                  {s?.expectancy_r == null ? "—" : formatR(s.expectancy_r)}
                </td>
                <td className="tabular py-2 pr-3 text-right">{num(s?.profit_factor)}</td>
                <td className="py-2 text-right">
                  {run.status !== "running" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${run.name}`}
                      onClick={() => remove.mutate(run.id)}
                    >
                      Delete
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
