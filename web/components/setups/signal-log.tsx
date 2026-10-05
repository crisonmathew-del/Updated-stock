"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Panel } from "@/components/admin/panel";
import { GradeBadge } from "@/components/setups/setups-board";
import { api, type SignalEntry, type SignalList } from "@/lib/api";
import { formatPctPoints, formatPrice, formatR } from "@/lib/format";
import { cn } from "@/lib/utils";

const HORIZONS = ["1", "5", "20", "60"];

function Return({ signal, horizon }: { signal: SignalEntry; horizon: string }) {
  const value = signal.outcome?.returns[horizon];
  const r = signal.outcome?.returns_r[horizon];
  if (value == null) return <span className="text-muted">—</span>;
  return (
    <>
      <span className={cn(value > 0 ? "text-ok" : value < 0 ? "text-fail" : "")}>
        {formatPctPoints(value)}
      </span>
      {r != null && <span className="block text-xs text-muted">{formatR(r)}</span>}
    </>
  );
}

function Hits({ signal }: { signal: SignalEntry }) {
  const o = signal.outcome;
  if (!o) return <span className="text-muted">not measured yet</span>;
  const hits = [
    o.gain_20_on && `+20% ${o.gain_20_on}`,
    o.target_2r_on && `2R ${o.target_2r_on}`,
    o.stop_hit_on && `stop ${o.stop_hit_on}`,
  ].filter(Boolean);
  return <>{hits.length ? hits.join(" · ") : "—"}</>;
}

/** The immutable signal log with each signal's outcome so far. */
export function SignalLog() {
  const [type, setType] = useState("");
  const params = new URLSearchParams({ limit: "200" });
  if (type) params.set("type", type);
  const list = useQuery({
    queryKey: ["signals", params.toString()],
    queryFn: () => api.get<SignalList>(`/api/signals?${params.toString()}`),
  });
  const counts = list.data?.counts ?? {};
  const labels = new Map(list.data?.items.map((s) => [s.type, s.type_label]));
  const total = Object.values(counts).reduce((n, c) => n + c, 0);

  return (
    <Panel
      title="Signal log"
      description="Every signal as it was logged at the close, never edited. Returns run from that close; R uses the plan's entry and stop."
    >
      <div role="group" aria-label="Signal type" className="flex flex-wrap gap-2 text-sm">
        {[
          ["", "All", total] as const,
          ...Object.entries(counts).map(([k, n]) => [k, labels.get(k) ?? k, n] as const),
        ].map(([value, label, n]) => (
          <button
            key={value || "all"}
            type="button"
            aria-pressed={type === value}
            onClick={() => setType(value)}
            className={cn(
              "rounded-md border px-3 py-1",
              type === value ? "border-foreground font-medium" : "border-border text-muted",
            )}
          >
            {label} <span className="tabular text-muted">{n}</span>
          </button>
        ))}
      </div>
      {list.isPending && <p className="text-sm text-muted">Loading signals…</p>}
      {list.error && <p className="text-sm text-fail">{list.error.message}</p>}
      {list.data && list.data.items.length === 0 && (
        <p className="text-sm text-muted">No signals logged yet.</p>
      )}
      {list.data && list.data.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted">
              <tr className="text-left">
                <th className="py-1 pr-3 font-normal">Date</th>
                <th className="py-1 pr-3 font-normal">Stock</th>
                <th className="py-1 pr-3 font-normal">Signal</th>
                <th className="py-1 pr-3 text-right font-normal">Close</th>
                <th className="py-1 pr-3 text-right font-normal">Grade</th>
                {HORIZONS.map((h) => (
                  <th key={h} className="py-1 pr-3 text-right font-normal">
                    +{h} {h === "1" ? "day" : "days"}
                  </th>
                ))}
                <th className="py-1 pr-3 text-right font-normal">Best / worst</th>
                <th className="py-1 font-normal">Reached</th>
              </tr>
            </thead>
            <tbody className="tabular divide-y divide-border">
              {list.data.items.map((s) => (
                <tr key={s.id} className="align-top">
                  <td className="py-1.5 pr-3 whitespace-nowrap">{s.date}</td>
                  <td className="py-1.5 pr-3 font-medium">{s.symbol ?? "Market"}</td>
                  <td className="py-1.5 pr-3">
                    {s.type_label}
                    <span className="block max-w-md text-xs text-muted">{s.summary}</span>
                  </td>
                  <td className="py-1.5 pr-3 text-right">{formatPrice(s.price)}</td>
                  <td className="py-1.5 pr-3 text-right">
                    {s.score == null ? "—" : <GradeBadge grade={s.grade} score={s.score} />}
                  </td>
                  {HORIZONS.map((h) => (
                    <td key={h} className="py-1.5 pr-3 text-right">
                      <Return signal={s} horizon={h} />
                    </td>
                  ))}
                  <td className="py-1.5 pr-3 text-right whitespace-nowrap">
                    {s.outcome?.mfe_pct == null
                      ? "—"
                      : `${formatPctPoints(s.outcome.mfe_pct)} / ${formatPctPoints(s.outcome.mae_pct)}`}
                  </td>
                  <td className="py-1.5 text-xs text-muted">
                    <Hits signal={s} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
