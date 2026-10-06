"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type SetupDetail } from "@/lib/api";
import { formatNumber, formatPrice } from "@/lib/format";
import { size, sizingSettings } from "@/lib/sizing";
import { Section, StatusMark } from "@/components/ui/section";
import { SETTINGS_QUERY } from "./queries";

export function ScoreCard({ setup }: { setup: SetupDetail }) {
  return (
    <Section
      title="Setup score"
      note={`${setup.raw_score.toFixed(1)} × ${setup.regime_multiplier} regime${setup.penalties ? ` − ${setup.penalties}` : ""} = ${setup.score.toFixed(1)}`}
    >
      <ul className="flex flex-col gap-1.5 text-sm">
        {setup.components.map((c) => (
          <li key={c.key} className="flex gap-2" title={c.detail}>
            <StatusMark status={c.status} />
            <span className="min-w-0 flex-1">
              {c.label}
              <span className="block truncate text-xs text-muted">{c.detail}</span>
            </span>
            <span className="tabular text-muted">
              {c.status === "no_data" ? "n/a" : `${c.points.toFixed(1)}/${c.max_points}`}
            </span>
          </li>
        ))}
      </ul>
      {setup.red_flag_details.length > 0 && (
        <ul className="flex flex-col gap-1 border-t border-border pt-2 text-sm">
          {setup.red_flag_details.map((f) => (
            <li key={f.key}>
              <span aria-hidden className="text-warn">
                ⚠{" "}
              </span>
              {f.label}
              {f.penalty ? ` (−${f.penalty})` : ""}
              <span className="block text-xs text-muted">{f.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      {label}
      <input
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="tabular h-8 w-full rounded-md border border-border bg-background px-2 text-sm text-foreground"
      />
    </label>
  );
}

/**
 * The plan from the scan, with entry and stop editable: shares, risk, position and targets
 * recalculate as you type (same rules as the server: lib/sizing.ts).
 */
export function PlanCard({ setup }: { setup: SetupDetail }) {
  const plan = setup.trade_plan;
  const settings = useQuery({
    queryKey: SETTINGS_QUERY.key,
    queryFn: () => api.get<{ items: { key: string; value: unknown }[] }>(SETTINGS_QUERY.path),
    staleTime: 10 * 60_000,
  });
  const [entry, setEntry] = useState(plan ? plan.entry.toFixed(2) : "");
  const [stop, setStop] = useState(plan ? plan.stop.toFixed(2) : "");
  if (!plan) {
    return (
      <Section title="Trade plan">
        <p className="text-sm text-muted">No plan: a watch setup has no base or pivot yet.</p>
      </Section>
    );
  }
  const s = settings.data ? sizingSettings(settings.data.items) : null;
  const e = Number.parseFloat(entry);
  const st = Number.parseFloat(stop);
  const sized = s ? size(e, st, s) : null;
  const edited = e !== plan.entry || st !== plan.stop;
  const currency = s?.account_currency ?? plan.currency;
  return (
    <Section
      title="Trade plan"
      note={
        edited
          ? "edited (not saved)"
          : plan.stop_basis === "logical"
            ? "stop below the base's last low"
            : "maximum-loss stop"
      }
    >
      <div className="grid grid-cols-2 gap-2">
        <NumberField label="Entry" value={entry} onChange={setEntry} />
        <NumberField label="Stop" value={stop} onChange={setStop} />
      </div>
      {sized ? (
        <dl className="tabular grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-muted">Shares</dt>
          <dd>
            {formatNumber(sized.shares)}{" "}
            <span className="text-muted">
              ({sized.positionPct}% of account{sized.cappedByPosition ? ", capped" : ""})
            </span>
          </dd>
          <dt className="text-muted">Position</dt>
          <dd>
            {currency} {formatNumber(sized.positionValue)}
          </dd>
          <dt className="text-muted">Risk</dt>
          <dd>
            {currency} {formatNumber(sized.dollarRisk)}{" "}
            <span className="text-muted">
              ({formatPrice(sized.riskPerShare)}/share, {sized.riskPct}%)
            </span>
          </dd>
          <dt className="text-muted">2R / 3R</dt>
          <dd>
            {formatPrice(sized.target2r)} / {formatPrice(sized.target3r)}
          </dd>
          <dt className="text-muted">Reward/risk</dt>
          <dd>{sized.rewardRisk} to the first profit level</dd>
          <dt className="text-muted">Take profits</dt>
          <dd>
            {formatPrice(plan.profit_take[0])} – {formatPrice(plan.profit_take[1])}
          </dd>
          <dt className="text-muted">Breakeven stop</dt>
          <dd>
            at {formatPrice(plan.breakeven_at)} ({plan.breakeven_basis})
          </dd>
          <dt className="text-muted">Trail</dt>
          <dd>
            close below {formatPrice(plan.trail_aggressive)} (21 EMA) or{" "}
            {formatPrice(plan.trail_standard)} (50 SMA)
          </dd>
        </dl>
      ) : (
        <p className="text-sm text-fail" role="alert">
          The stop must be below the entry, and both must be positive numbers.
        </p>
      )}
      {sized?.stopTooWide && (
        <p className="text-sm text-warn">
          <span aria-hidden>⚠ </span>The stop is {sized.riskPct}% below the entry, wider than the{" "}
          {s?.max_stop_loss_pct}% maximum loss.
        </p>
      )}
      {!edited &&
        plan.notes.map((n) => (
          <p key={n} className="text-sm text-warn">
            <span aria-hidden>⚠ </span>
            {n}
          </p>
        ))}
      {edited && (
        <button
          type="button"
          onClick={() => {
            setEntry(plan.entry.toFixed(2));
            setStop(plan.stop.toFixed(2));
          }}
          className="self-start text-xs text-muted underline underline-offset-2 hover:text-foreground"
        >
          Reset to the scan&apos;s plan
        </button>
      )}
    </Section>
  );
}
