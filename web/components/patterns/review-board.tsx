"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Panel } from "@/components/admin/panel";
import { PatternCard } from "@/components/patterns/pattern-card";
import { api, type Pattern, type ReviewStats, type Verdict } from "@/lib/api";
import { formatRate } from "@/lib/format";
import { cn } from "@/lib/utils";

const VERDICTS: { value: Verdict; label: string; symbol: string }[] = [
  { value: "correct", label: "Correct", symbol: "✓" },
  { value: "wrong", label: "Wrong", symbol: "✕" },
  { value: "unsure", label: "Unsure", symbol: "?" },
];

function StatsTable({ stats }: { stats: ReviewStats }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs text-muted">
          <tr className="text-left">
            <th className="py-1 pr-3 font-normal">Pattern</th>
            <th className="py-1 pr-3 text-right font-normal">Detected</th>
            <th className="py-1 pr-3 text-right font-normal">Reviewed</th>
            <th className="py-1 pr-3 text-right font-normal">✓ Correct</th>
            <th className="py-1 pr-3 text-right font-normal">✕ Wrong</th>
            <th className="py-1 pr-3 text-right font-normal">? Unsure</th>
            <th className="py-1 text-right font-normal">False positives</th>
          </tr>
        </thead>
        <tbody className="tabular divide-y divide-border">
          {stats.types.map((t) => (
            <tr key={t.type}>
              <td className="py-1 pr-3">{t.type_label}</td>
              <td className="py-1 pr-3 text-right">{t.detected}</td>
              <td className="py-1 pr-3 text-right">{t.reviewed}</td>
              <td className="py-1 pr-3 text-right">{t.correct}</td>
              <td className="py-1 pr-3 text-right">{t.wrong}</td>
              <td className="py-1 pr-3 text-right">{t.unsure}</td>
              <td className="py-1 text-right">{formatRate(t.false_positive_rate)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot className="tabular text-sm font-medium">
          <tr>
            <td className="py-1 pr-3">All</td>
            <td className="py-1 pr-3 text-right">
              {stats.types.reduce((n, t) => n + t.detected, 0)}
            </td>
            <td className="py-1 pr-3 text-right">{stats.reviewed}</td>
            <td colSpan={3} />
            <td className="py-1 text-right">{formatRate(stats.false_positive_rate)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function VerdictControls({ pattern }: { pattern: Pattern }) {
  const client = useQueryClient();
  const [note, setNote] = useState(pattern.review?.note ?? "");
  const save = useMutation({
    mutationFn: async (verdict: Verdict | null): Promise<void> => {
      if (verdict === null) {
        await api.delete(`/api/admin/patterns/${pattern.id}/review`);
        return;
      }
      await api.put<Pattern>(`/api/admin/patterns/${pattern.id}/review`, {
        verdict,
        note: note.trim() || null,
      });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["patterns"] }),
  });
  const current = pattern.review?.verdict;
  return (
    <div className="flex flex-col gap-2">
      <div
        role="group"
        aria-label={`Verdict for ${pattern.symbol}`}
        className="flex flex-wrap gap-2"
      >
        {VERDICTS.map((v) => (
          <button
            key={v.value}
            type="button"
            aria-pressed={current === v.value}
            disabled={save.isPending}
            onClick={() => save.mutate(v.value)}
            className={cn(
              "rounded-md border px-3 py-1.5 text-sm",
              current === v.value ? "border-foreground font-medium" : "border-border text-muted",
            )}
          >
            <span aria-hidden>{v.symbol}</span> {v.label}
          </button>
        ))}
        {current && (
          <button
            type="button"
            onClick={() => save.mutate(null)}
            disabled={save.isPending}
            className="px-2 text-sm text-muted underline-offset-2 hover:underline"
          >
            Clear
          </button>
        )}
      </div>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Note (optional, saved with the verdict)
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
        />
      </label>
      {save.error && <p className="text-sm text-fail">{save.error.message}</p>}
    </div>
  );
}

export function ReviewBoard() {
  const [seed, setSeed] = useState(1);
  const [draft, setDraft] = useState("1");
  const [unreviewed, setUnreviewed] = useState(false);
  const stats = useQuery({
    queryKey: ["patterns", "stats"],
    queryFn: () => api.get<ReviewStats>("/api/admin/patterns/review-stats"),
  });
  const sample = useQuery({
    queryKey: ["patterns", "sample", seed, unreviewed],
    queryFn: () =>
      api.get<Pattern[]>(
        `/api/admin/patterns/sample?size=20&seed=${seed}${unreviewed ? "&unreviewed=true" : ""}`,
      ),
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number.parseInt(draft, 10);
    setSeed(Number.isFinite(value) && value >= 0 ? value : 1);
  }

  return (
    <>
      <Panel
        title="False-positive review"
        description="Mark each detection correct or wrong as drawn. The rate is wrong ÷ (correct + wrong); unsure verdicts don't count."
      >
        {stats.error && <p className="text-sm text-fail">{stats.error.message}</p>}
        {stats.data &&
          (stats.data.types.length === 0 ? (
            <p className="text-sm text-muted">
              Nothing detected yet. Patterns are found after each EOD update (or{" "}
              <code className="text-xs">make patterns</code>).
            </p>
          ) : (
            <StatsTable stats={stats.data} />
          ))}
      </Panel>

      <Panel
        title="Random sample"
        description="20 detections spread across pattern types. The same sample number always draws the same detections."
        actions={
          <form onSubmit={onSubmit} className="flex items-center gap-2 text-sm">
            <label htmlFor="sample-seed" className="text-muted">
              Sample
            </label>
            <input
              id="sample-seed"
              inputMode="numeric"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="tabular w-20 rounded-md border border-border bg-background px-2 py-1"
            />
            <button type="submit" className="rounded-md border border-border px-3 py-1">
              Draw
            </button>
            <label className="flex items-center gap-1 text-muted">
              <input
                type="checkbox"
                checked={unreviewed}
                onChange={(e) => setUnreviewed(e.target.checked)}
              />
              Only unreviewed
            </label>
          </form>
        }
      >
        {sample.isPending && <p className="text-sm text-muted">Drawing a sample…</p>}
        {sample.error && <p className="text-sm text-fail">{sample.error.message}</p>}
        {sample.data && sample.data.length === 0 && (
          <p className="text-sm text-muted">No detections to review.</p>
        )}
        <div className="flex flex-col gap-4">
          {sample.data?.map((p) => (
            <PatternCard key={p.id} pattern={p}>
              <VerdictControls pattern={p} />
            </PatternCard>
          ))}
        </div>
      </Panel>
    </>
  );
}
