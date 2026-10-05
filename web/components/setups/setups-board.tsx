"use client";

import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Panel } from "@/components/admin/panel";
import { SetupDetailView } from "@/components/setups/setup-detail";
import { api, type SetupList, type SetupRow, type SetupState } from "@/lib/api";
import { formatNumber, formatPrice, formatReadiness } from "@/lib/format";
import { cn } from "@/lib/utils";

const STAGES: { value: SetupState; label: string }[] = [
  { value: "watch", label: "Watch" },
  { value: "basing", label: "Basing" },
  { value: "near_pivot", label: "Near pivot" },
  { value: "breakout", label: "Breakout" },
  { value: "extended", label: "Extended" },
];
const GRADES = [
  { value: "", label: "Any grade" },
  { value: "top", label: "A and A+" },
  { value: "A+", label: "A+" },
  { value: "A", label: "A" },
  { value: "B", label: "B" },
  { value: "C", label: "C" },
];
const SORTS = [
  { value: "score", label: "Score" },
  { value: "readiness", label: "Closest to the pivot" },
  { value: "recent", label: "Latest stage change" },
  { value: "symbol", label: "Symbol" },
];

export function GradeBadge({ grade, score }: { grade: string | null; score: number }) {
  const cls =
    grade === "A+" || grade === "A" ? "text-ok" : grade === "C" ? "text-warn" : "text-foreground";
  return (
    <span className="tabular">
      <span className={cn("font-medium", grade ? cls : "text-muted")}>{grade ?? "—"}</span>{" "}
      <span className="text-muted">{score.toFixed(0)}</span>
    </span>
  );
}

function setupName(s: SetupRow): string {
  if (s.kind === "watch") return "Trend leader, no base";
  return s.pattern_label ?? "Base";
}

export function SetupsBoard() {
  const [stage, setStage] = useState<SetupState | "">("");
  const [grade, setGrade] = useState("");
  const [sort, setSort] = useState("score");
  const [closed, setClosed] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  const params = new URLSearchParams({ sort, limit: "200" });
  if (stage) params.set("state", stage);
  if (grade) params.set("grade", grade);
  if (closed) params.set("active", "false");
  const list = useQuery({
    queryKey: ["setups", "list", params.toString()],
    queryFn: () => api.get<SetupList>(`/api/setups?${params.toString()}`),
  });
  const counts = list.data?.counts ?? {};
  const active = Object.values(counts).reduce((n, c) => n + (c ?? 0), 0);

  return (
    <Panel
      title="Setups"
      description={
        list.data?.as_of
          ? `As of the close of ${list.data.as_of}. Open a row for the score breakdown, plan and history.`
          : "Setups appear after the first EOD scan (or make setups)."
      }
      actions={
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1 text-muted">
            Grade
            <select
              value={grade}
              onChange={(e) => setGrade(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 text-foreground"
            >
              {GRADES.map((g) => (
                <option key={g.value} value={g.value}>
                  {g.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1 text-muted">
            Sort
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 text-foreground"
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1 text-muted">
            <input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)} />
            Closed setups
          </label>
        </div>
      }
    >
      <div role="group" aria-label="Stage" className="flex flex-wrap gap-2 text-sm">
        {[{ value: "" as const, label: "All" }, ...STAGES].map((s) => (
          <button
            key={s.value || "all"}
            type="button"
            aria-pressed={stage === s.value}
            onClick={() => setStage(s.value)}
            className={cn(
              "rounded-md border px-3 py-1",
              stage === s.value ? "border-foreground font-medium" : "border-border text-muted",
            )}
          >
            {s.label}{" "}
            <span className="tabular text-muted">{s.value ? (counts[s.value] ?? 0) : active}</span>
          </button>
        ))}
      </div>
      {list.isPending && <p className="text-sm text-muted">Loading setups…</p>}
      {list.error && <p className="text-sm text-fail">{list.error.message}</p>}
      {list.data && list.data.items.length === 0 && (
        <p className="text-sm text-muted">No setups match.</p>
      )}
      {list.data && list.data.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted">
              <tr className="text-left">
                <th className="py-1 pr-3 font-normal">Stock</th>
                <th className="py-1 pr-3 font-normal">Setup</th>
                <th className="py-1 pr-3 font-normal">Stage</th>
                <th className="py-1 pr-3 text-right font-normal">Grade</th>
                <th className="py-1 pr-3 text-right font-normal">Pivot</th>
                <th className="py-1 pr-3 text-right font-normal">Close vs pivot</th>
                <th className="py-1 pr-3 text-right font-normal">Entry / stop</th>
                <th className="py-1 pr-3 text-right font-normal">Shares</th>
                <th className="py-1 font-normal">Red flags</th>
              </tr>
            </thead>
            <tbody className="tabular divide-y divide-border">
              {list.data.items.map((s) => (
                <Fragment key={s.id}>
                  <tr>
                    <td className="py-1.5 pr-3">
                      <button
                        type="button"
                        aria-expanded={open === s.id}
                        onClick={() => setOpen(open === s.id ? null : s.id)}
                        className="font-medium underline-offset-2 hover:underline"
                      >
                        <span aria-hidden>{open === s.id ? "▾" : "▸"} </span>
                        {s.symbol}
                      </button>
                    </td>
                    <td className="py-1.5 pr-3">{setupName(s)}</td>
                    <td className="py-1.5 pr-3">
                      {s.state_label}
                      <span className="block text-xs text-muted">
                        {s.active ? `since ${s.state_since}` : `closed ${s.closed_on}`}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 text-right">
                      <GradeBadge grade={s.grade} score={s.score} />
                    </td>
                    <td className="py-1.5 pr-3 text-right">{formatPrice(s.pivot)}</td>
                    <td className="py-1.5 pr-3 text-right">{formatReadiness(s.readiness_pct)}</td>
                    <td className="py-1.5 pr-3 text-right">
                      {formatPrice(s.entry)} / {formatPrice(s.stop)}
                      {s.risk_too_wide && (
                        <span className="block text-xs text-warn">⚠ risk too wide</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-right">
                      {s.shares == null ? "—" : formatNumber(s.shares)}
                    </td>
                    <td className="py-1.5 text-xs text-muted">
                      {s.red_flags.length ? `⚠ ${s.red_flags.join(", ")}` : "—"}
                    </td>
                  </tr>
                  {open === s.id && (
                    <tr>
                      <td colSpan={9} className="py-4">
                        <SetupDetailView id={s.id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          {list.data.total > list.data.items.length && (
            <p className="mt-2 text-xs text-muted">
              Showing {list.data.items.length} of {list.data.total}.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
