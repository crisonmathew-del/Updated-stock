"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type BackfillProgress, type Enqueued } from "@/lib/api";
import { formatDateTime, formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ActionButton, Panel, Stat } from "./panel";

const ACTIONS = [
  { label: "Rebuild universe", path: "/api/admin/universe", done: "Universe rebuild queued." },
  { label: "Run backfill", path: "/api/admin/backfill", done: "Backfill queued." },
  { label: "Run EOD update", path: "/api/admin/eod-update", done: "End-of-day update queued." },
  { label: "Run quality checks", path: "/api/admin/data-quality", done: "Quality checks queued." },
] as const;

function statusLine(p: BackfillProgress): string {
  const counts = `${formatNumber(p.processed)} of ${formatNumber(p.total)} tickers`;
  switch (p.status) {
    case "running":
      return `Running · ${counts} (${formatPercent(p.processed, p.total)})`;
    case "succeeded":
      return `Finished ${formatDateTime(p.updated_at)} · ${counts}`;
    case "failed":
      return `Stopped ${formatDateTime(p.updated_at)} · ${counts}`;
    default:
      return "No backfill has run yet.";
  }
}

export function BackfillPanel() {
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const { data: progress } = useQuery({
    queryKey: ["admin", "backfill"],
    queryFn: () => api.get<BackfillProgress>("/api/admin/backfill"),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2_000 : 15_000),
  });
  const action = useMutation({
    mutationFn: (path: string) => api.post<Enqueued>(path),
    onSuccess: (_, path) => {
      setFeedback({ ok: true, text: ACTIONS.find((a) => a.path === path)?.done ?? "Queued." });
      void queryClient.invalidateQueries({ queryKey: ["admin"] });
    },
    onError: (error) => setFeedback({ ok: false, text: error.message }),
  });

  return (
    <Panel
      title="Backfill"
      description="Daily history for every ticker. Runs resume where they stopped."
      actions={ACTIONS.map((a) => (
        <ActionButton
          key={a.path}
          onClick={() => action.mutate(a.path)}
          disabled={action.isPending}
        >
          {a.label}
        </ActionButton>
      ))}
    >
      {feedback && (
        <p role="status" className={cn("text-sm", feedback.ok ? "text-ok" : "text-fail")}>
          {feedback.text}
        </p>
      )}
      {progress && (
        <div className="flex flex-col gap-3">
          <p className="tabular text-sm">{statusLine(progress)}</p>
          {progress.total > 0 && (
            <div
              role="progressbar"
              aria-label="Backfill progress"
              aria-valuemin={0}
              aria-valuemax={progress.total}
              aria-valuenow={progress.processed}
              className="h-2 overflow-hidden rounded-full bg-border"
            >
              <div
                className={cn(
                  "h-full transition-[width] motion-reduce:transition-none",
                  progress.status === "failed" ? "bg-fail" : "bg-ok",
                )}
                style={{ width: formatPercent(progress.processed, progress.total) }}
              />
            </div>
          )}
          {progress.status !== "idle" && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
              <Stat label="Loaded" value={formatNumber(progress.done)} />
              <Stat label="No data" value={formatNumber(progress.no_data)} />
              <Stat label="Failed" value={formatNumber(progress.failed)} />
              <Stat label="Bars written" value={formatNumber(progress.bars_written)} />
            </dl>
          )}
          {progress.status === "running" && progress.current.length > 0 && (
            <p className="text-xs text-muted">Now fetching {progress.current.join(", ")}</p>
          )}
          {progress.message && <p className="text-sm text-fail">{progress.message}</p>}
          {progress.start && progress.end && (
            <p className="text-xs text-muted">
              Range {progress.start} → {progress.end}
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
