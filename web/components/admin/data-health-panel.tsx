"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type DataHealth, type Severity } from "@/lib/api";
import { formatDateTime, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Panel, SeverityBadge } from "./panel";

const FILTERS: (Severity | "all")[] = ["all", "critical", "warning", "info"];

// Checks whose names contain acronyms; everything else reads fine humanized.
const CHECK_LABELS: Record<string, string> = {
  ohlc_inconsistent: "OHLC inconsistent",
  sec_not_configured: "SEC not configured",
};

function checkLabel(check: string): string {
  return CHECK_LABELS[check] ?? humanize(check);
}

export function DataHealthPanel() {
  const [filter, setFilter] = useState<Severity | "all">("all");
  const { data, error } = useQuery({
    queryKey: ["admin", "data-health", filter],
    queryFn: () =>
      api.get<DataHealth>(
        filter === "all" ? "/api/admin/data-health" : `/api/admin/data-health?severity=${filter}`,
      ),
    refetchInterval: 30_000,
  });

  return (
    <Panel
      title="Data health"
      description={
        data?.last_check
          ? `Last checked ${formatDateTime(data.last_check.started_at)} (${data.last_check.job_name.replace("_", " ")}).`
          : "Checks run after every end-of-day update."
      }
    >
      {error && <p className="text-sm text-fail">{error.message}</p>}
      {data && (
        <>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            {data.summary.critical === 0 ? (
              <span className="inline-flex items-center gap-1.5 text-sm font-medium text-ok">
                <span aria-hidden>✓</span> No critical issues
              </span>
            ) : (
              <SeverityBadge severity="critical" count={data.summary.critical} />
            )}
            <SeverityBadge severity="warning" count={data.summary.warning} />
            <SeverityBadge severity="info" count={data.summary.info} />
          </div>

          <div role="group" aria-label="Filter by severity" className="flex gap-1">
            {FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={filter === f}
                onClick={() => setFilter(f)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs",
                  filter === f
                    ? "bg-foreground text-background"
                    : "text-muted hover:text-foreground",
                )}
              >
                {f === "all" ? "All" : humanize(f)}
              </button>
            ))}
          </div>

          {data.issues.length === 0 ? (
            <p className="text-sm text-muted">
              {filter === "all" ? "No open issues." : `No open ${filter} issues.`}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-2 pr-4 font-normal">Severity</th>
                    <th className="py-2 pr-4 font-normal">Check</th>
                    <th className="py-2 pr-4 font-normal">Ticker</th>
                    <th className="py-2 pr-4 font-normal">Date</th>
                    <th className="py-2 font-normal">What we found</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {data.issues.map((issue) => (
                    <tr key={issue.id} className="align-top">
                      <td className="py-2 pr-4 whitespace-nowrap">
                        <SeverityBadge severity={issue.severity} />
                      </td>
                      <td className="py-2 pr-4 whitespace-nowrap">{checkLabel(issue.check)}</td>
                      <td className="py-2 pr-4 font-medium">{issue.symbol ?? "—"}</td>
                      <td className="tabular py-2 pr-4 whitespace-nowrap">
                        {issue.issue_date ?? "—"}
                      </td>
                      <td className="py-2">{issue.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
