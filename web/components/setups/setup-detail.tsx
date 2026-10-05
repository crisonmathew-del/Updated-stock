"use client";

import { useQuery } from "@tanstack/react-query";
import { PatternCard } from "@/components/patterns/pattern-card";
import { api, type ScoreComponent, type SetupDetail, type TradePlan } from "@/lib/api";
import { formatNumber, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS: Record<ScoreComponent["status"], { symbol: string; label: string; cls: string }> = {
  pass: { symbol: "✓", label: "full points", cls: "text-ok" },
  partial: { symbol: "~", label: "partial", cls: "text-warn" },
  fail: { symbol: "✕", label: "no points", cls: "text-fail" },
  no_data: { symbol: "–", label: "no data, left out", cls: "text-muted" },
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-medium">{title}</h4>
      {children}
    </section>
  );
}

function ScoreBreakdown({ setup }: { setup: SetupDetail }) {
  return (
    <Section title="Score">
      <p className="tabular text-sm text-muted">
        {setup.raw_score.toFixed(1)} × {setup.regime_multiplier} regime
        {setup.penalties > 0 ? ` − ${setup.penalties} red flags` : ""} ={" "}
        <span className="text-foreground">{setup.score.toFixed(1)}</span>
        {setup.grade ? ` (grade ${setup.grade})` : " (below C: not a recommendation)"}
      </p>
      <ul className="divide-y divide-border text-sm">
        {setup.components.map((c) => {
          const s = STATUS[c.status];
          return (
            <li key={c.key} className="flex gap-3 py-1.5">
              <span className={cn("w-4", s.cls)} aria-label={s.label}>
                {s.symbol}
              </span>
              <span className="flex-1">
                {c.label}
                <span className="block text-xs text-muted">{c.detail}</span>
              </span>
              <span className="tabular text-muted">
                {c.status === "no_data" ? "n/a" : `${c.points.toFixed(1)}/${c.max_points}`}
              </span>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function Plan({ plan }: { plan: TradePlan }) {
  const rows: [string, string][] = [
    ["Entry", formatPrice(plan.entry)],
    [
      "Stop",
      `${formatPrice(plan.stop)} (${plan.stop_basis === "logical" ? "below the base's last low" : "maximum loss"}), ${plan.risk_pct}% risk`,
    ],
    ["Shares", `${formatNumber(plan.shares)} (${plan.position_pct}% of the account)`],
    ["Position", `${plan.currency} ${formatNumber(plan.position_value)}`],
    ["Risk", `${plan.currency} ${formatNumber(plan.dollar_risk)}`],
    ["Buy zone", `${formatPrice(plan.buy_zone[0])} – ${formatPrice(plan.buy_zone[1])}`],
    ["2R / 3R", `${formatPrice(plan.target_2r)} / ${formatPrice(plan.target_3r)}`],
    ["Take profits", `${formatPrice(plan.profit_take[0])} – ${formatPrice(plan.profit_take[1])}`],
    ["Stop to breakeven", `at ${formatPrice(plan.breakeven_at)} (${plan.breakeven_basis})`],
    [
      "Trail",
      `close below ${formatPrice(plan.trail_aggressive)} (21-day EMA) or ${formatPrice(plan.trail_standard)} (50-day SMA)`,
    ],
    ["Reward / risk", `${plan.reward_risk} to the first profit level`],
  ];
  return (
    <Section title="Trade plan">
      <dl className="tabular grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {plan.notes.map((n) => (
        <p key={n} className="text-sm text-warn">
          <span aria-hidden>⚠ </span>
          {n}
        </p>
      ))}
    </Section>
  );
}

/** Everything behind one setup: score parts, red flags, plan, stage history, signals, chart. */
export function SetupDetailView({ id }: { id: number }) {
  const detail = useQuery({
    queryKey: ["setups", "detail", id],
    queryFn: () => api.get<SetupDetail>(`/api/setups/${id}`),
  });
  if (detail.isPending) return <p className="text-sm text-muted">Loading the setup…</p>;
  if (detail.error) return <p className="text-sm text-fail">{detail.error.message}</p>;
  const s = detail.data;
  return (
    <div className="flex flex-col gap-5" aria-label={`${s.symbol} setup details`}>
      <div className="grid gap-5 lg:grid-cols-2">
        <ScoreBreakdown setup={s} />
        <div className="flex flex-col gap-5">
          <Section title="Red flags">
            {s.red_flag_details.length === 0 ? (
              <p className="text-sm text-muted">None.</p>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {s.red_flag_details.map((f) => (
                  <li key={f.key}>
                    <span aria-hidden className="text-warn">
                      ⚠{" "}
                    </span>
                    {f.label}
                    {f.penalty > 0 ? ` (−${f.penalty})` : ""}:{" "}
                    <span className="text-muted">{f.detail}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          {s.trade_plan ? (
            <Plan plan={s.trade_plan} />
          ) : (
            <Section title="Trade plan">
              <p className="text-sm text-muted">No plan: a watch setup has no pivot yet.</p>
            </Section>
          )}
        </div>
      </div>
      <Section title="History">
        <ol className="flex flex-col gap-1 text-sm">
          {s.transitions.map((t, i) => (
            <li key={i} className="tabular">
              <span className="text-muted">{t.date}</span> {t.to_label}:{" "}
              <span className="text-muted">{t.reason}</span>
            </li>
          ))}
        </ol>
        {s.closed_reason && (
          <p className="text-sm">
            Closed {s.closed_on}: <span className="text-muted">{s.closed_reason}</span>
          </p>
        )}
      </Section>
      {s.signals.length > 0 && (
        <Section title="Signals">
          <ul className="flex flex-col gap-1 text-sm">
            {s.signals.map((sig) => (
              <li key={sig.id} className="tabular">
                <span className="text-muted">{sig.date}</span> {sig.type_label}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {s.pattern && <PatternCard pattern={s.pattern} />}
    </div>
  );
}
