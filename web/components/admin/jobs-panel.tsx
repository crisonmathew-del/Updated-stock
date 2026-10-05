"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type JobRun } from "@/lib/api";
import { formatDateTime, formatDuration, jobLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Panel } from "./panel";

const STATUS: Record<JobRun["status"], { symbol: string; className: string }> = {
  running: { symbol: "…", className: "text-muted" },
  succeeded: { symbol: "✓", className: "text-ok" },
  failed: { symbol: "✕", className: "text-fail" },
  cancelled: { symbol: "–", className: "text-muted" },
};

function summary(run: JobRun): string {
  if (run.error) return run.error;
  const s = run.stats;
  if (run.job_name === "backfill" || s.done !== undefined) {
    return `${s.done ?? 0} loaded · ${s.no_data ?? 0} no data · ${s.failed ?? 0} failed`;
  }
  if (run.job_name === "eod_update")
    return `Session ${s.session ?? "?"} · ${s.tickers ?? 0} tickers`;
  if (run.job_name === "universe") return `${s.universe ?? 0} in universe · ${s.added ?? 0} added`;
  if (run.job_name === "data_quality") {
    return `${s.critical ?? 0} critical · ${s.warning ?? 0} warning · ${s.info ?? 0} info`;
  }
  return "";
}

export function JobsPanel() {
  const { data, error } = useQuery({
    queryKey: ["admin", "jobs"],
    queryFn: () => api.get<JobRun[]>("/api/admin/jobs?limit=15"),
    refetchInterval: 10_000,
  });

  return (
    <Panel title="Recent jobs" description="Every background run, newest first.">
      {error && <p className="text-sm text-fail">{error.message}</p>}
      {data && data.length === 0 && <p className="text-sm text-muted">No jobs have run yet.</p>}
      {data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-2 pr-4 font-normal">Job</th>
                <th className="py-2 pr-4 font-normal">Status</th>
                <th className="py-2 pr-4 font-normal">Started</th>
                <th className="py-2 pr-4 font-normal">Took</th>
                <th className="py-2 font-normal">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((run) => (
                <tr key={run.id} className="align-top">
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {jobLabel(run.job_name)}
                    <span className="text-muted"> · {run.trigger}</span>
                  </td>
                  <td className={cn("py-2 pr-4 whitespace-nowrap", STATUS[run.status].className)}>
                    <span aria-hidden>{STATUS[run.status].symbol}</span> {run.status}
                  </td>
                  <td className="tabular py-2 pr-4 whitespace-nowrap">
                    {formatDateTime(run.started_at)}
                  </td>
                  <td className="tabular py-2 pr-4">{formatDuration(run.duration_ms)}</td>
                  <td className="py-2 text-muted">{summary(run)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
