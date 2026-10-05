"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type FiscalPeriod, type Fundamentals, type GradeComponent } from "@/lib/api";
import { formatCompact, formatNumber, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

const MARKS: Record<
  GradeComponent["status"],
  { symbol: string; label: string; className: string }
> = {
  pass: { symbol: "✓", label: "Pass", className: "text-ok" },
  partial: { symbol: "◐", label: "Partial", className: "text-warn" },
  fail: { symbol: "✕", label: "Fail", className: "text-fail" },
  no_data: { symbol: "–", label: "No data", className: "text-muted" },
};

function growth(value: number | null, note: string | null): string {
  if (note === "turnaround") return "turnaround";
  if (note === "loss") return "loss";
  if (value == null) return "—";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(1)}%`;
}

function PeriodTable({ caption, rows }: { caption: string; rows: FiscalPeriod[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="mb-1 text-left text-sm font-medium">{caption}</caption>
        <thead className="text-xs text-muted">
          <tr className="text-left">
            <th className="py-1 pr-3 font-normal">Period</th>
            <th className="py-1 pr-3 font-normal">Filed</th>
            <th className="py-1 pr-3 text-right font-normal">EPS</th>
            <th className="py-1 pr-3 text-right font-normal">EPS vs year earlier</th>
            <th className="py-1 pr-3 text-right font-normal">Sales</th>
            <th className="py-1 text-right font-normal">Sales vs year earlier</th>
          </tr>
        </thead>
        <tbody className="tabular divide-y divide-border">
          {rows.map((p) => (
            <tr key={p.period_end}>
              <td className="py-1 pr-3">
                {p.label}
                {p.derived && (
                  <span
                    title="Derived: full year minus the first nine months"
                    className="text-muted"
                  >
                    {" "}
                    †
                  </span>
                )}
              </td>
              <td className="py-1 pr-3 text-muted">{p.reported_date}</td>
              <td className="py-1 pr-3 text-right">{formatPrice(p.eps)}</td>
              <td className="py-1 pr-3 text-right">{growth(p.eps_growth_pct, p.eps_note)}</td>
              <td className="py-1 pr-3 text-right">{formatCompact(p.revenue)}</td>
              <td className="py-1 text-right">{growth(p.revenue_growth_pct, null)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FundamentalsSection({ symbol }: { symbol: string }) {
  const { data, error, isPending } = useQuery({
    queryKey: ["fundamentals", symbol],
    queryFn: () => api.get<Fundamentals>(`/api/stocks/${encodeURIComponent(symbol)}/fundamentals`),
  });
  if (isPending) return <p className="text-sm text-muted">Loading fundamentals…</p>;
  if (error) return <p className="text-sm text-fail">{error.message}</p>;

  const grade = data.grade;
  const next = data.earnings.find((e) => e.status === "estimated");
  const reported = data.earnings.filter((e) => e.status === "reported").slice(0, 4);
  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-sm font-medium">Fundamentals</h3>
      {data.refreshed_at == null && (
        <p className="text-sm text-muted">
          Statements for {data.symbol} aren&apos;t loaded yet. Run{" "}
          <code className="text-xs">make fundamentals symbols={data.symbol}</code>.
        </p>
      )}
      {grade && (
        <div className="flex flex-col gap-3">
          <div className="flex items-baseline gap-3">
            <span className="text-3xl font-semibold" aria-label={`Grade ${grade.grade ?? "n/a"}`}>
              {grade.grade ?? "n/a"}
            </span>
            <span className="tabular text-sm text-muted">
              {grade.score != null ? `${grade.score.toFixed(0)}/100 · ` : ""}
              {grade.path === "eps" ? "EPS path" : "Revenue-led path (no positive EPS)"} ·{" "}
              {grade.basis === "annual" ? "annual data only" : `${grade.basis} data`} ·{" "}
              {grade.coverage_pct.toFixed(0)}% of points have data
            </span>
          </div>
          <ul className="divide-y divide-border rounded-md border border-border">
            {grade.components.map((c) => {
              const mark = MARKS[c.status];
              return (
                <li key={c.key} className="flex gap-3 px-3 py-2 text-sm">
                  <span aria-hidden className={cn("w-4 font-semibold", mark.className)}>
                    {mark.symbol}
                  </span>
                  <span className="flex-1">
                    <span className="sr-only">{mark.label}: </span>
                    {c.label}
                    {c.bonus && <span className="text-muted"> (bonus)</span>}
                    <span className="tabular block text-xs text-muted">{c.detail}</span>
                  </span>
                  <span className="tabular text-sm text-muted">
                    {c.points.toFixed(1)}/{c.max_points}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <PeriodTable caption="Quarters (as reported by the date above)" rows={data.quarters} />
      <PeriodTable caption="Fiscal years" rows={data.years} />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1 text-sm">
          <h4 className="font-medium">Earnings dates</h4>
          <p>
            Next:{" "}
            {next ? (
              <span className="tabular">{next.report_date} (estimated from last year)</span>
            ) : (
              <span className="text-muted">no estimate</span>
            )}
          </p>
          {reported.length > 0 && (
            <p className="tabular text-muted">
              Reported:{" "}
              {reported.map((e) => `${e.report_date} (${e.timing.replace("_", " ")})`).join(", ")}
            </p>
          )}
        </div>
        <div className="flex flex-col gap-1 text-sm">
          <h4 className="font-medium">Insider open-market trades</h4>
          {data.insiders.length === 0 ? (
            <p className="text-muted">None recorded.</p>
          ) : (
            <ul className="tabular flex flex-col gap-0.5">
              {data.insiders.slice(0, 6).map((t, i) => (
                <li key={`${t.transaction_date}-${i}`}>
                  <span className={t.code === "P" ? "text-ok" : "text-fail"}>
                    {t.code === "P" ? "▲ Bought" : "▼ Sold"}
                  </span>{" "}
                  {formatNumber(Math.round(t.shares))} at {formatPrice(t.price)} · {t.insider_name}{" "}
                  <span className="text-muted">
                    ({t.role}, {t.transaction_date})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
