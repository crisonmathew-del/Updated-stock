"use client";

import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Change } from "@/components/ui/badges";
import { Section } from "@/components/ui/section";
import { api, type Performance, type PerformanceStats, type PerformanceType } from "@/lib/api";
import { formatNumber, formatR } from "@/lib/format";
import { cn } from "@/lib/utils";
import { pct, regimeLabel } from "@/components/backtests/format";

const HORIZONS = [5, 10, 20, 60] as const;
const WINDOWS = [
  { key: "all", label: "All", months: null },
  { key: "12m", label: "12 months", months: 12 },
  { key: "6m", label: "6 months", months: 6 },
  { key: "3m", label: "3 months", months: 3 },
] as const;

export function sinceFor(months: number | null, today = new Date()): string | null {
  if (months == null) return null;
  const d = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - months, today.getUTCDate()),
  );
  return d.toISOString().slice(0, 10);
}

export function performancePath(horizon: number, since: string | null): string {
  const params = new URLSearchParams({ horizon: String(horizon) });
  if (since) params.set("since", since);
  return `/api/performance?${params.toString()}`;
}

function Toggle<T extends string | number>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { key: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap items-center gap-1">
      <span className="mr-1 text-sm text-muted">{label}</span>
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          role="radio"
          aria-checked={value === o.key}
          onClick={() => onChange(o.key)}
          className={cn(
            "rounded-md border px-2 py-0.5 text-sm",
            value === o.key ? "border-tide text-foreground" : "border-border text-muted",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Cells({ s, r }: { s: PerformanceStats; r: boolean }) {
  return (
    <>
      <td className="tabular py-1.5 pr-3 text-right">
        {formatNumber(s.signals)}
        {s.measured !== s.signals && (
          <span className="text-muted"> ({formatNumber(s.measured)} measured)</span>
        )}
      </td>
      <td className="tabular py-1.5 pr-3 text-right">{pct(s.win_rate_pct, 0)}</td>
      <td className="tabular py-1.5 pr-3 text-right">
        <Change value={s.avg_return_pct} digits={1} />
      </td>
      <td className="tabular py-1.5 pr-3 text-right">
        <Change value={s.avg_gain_pct} digits={1} /> / <Change value={s.avg_loss_pct} digits={1} />
      </td>
      <td className="tabular py-1.5 pr-3 text-right">
        {r && s.expectancy_r != null ? formatR(s.expectancy_r) || "0.00R" : "—"}
      </td>
      <td className="tabular py-1.5 pr-3 text-right">{pct(s.stop_hit_pct, 0)}</td>
      <td className="tabular py-1.5 pr-3 text-right">{pct(s.reached_20_pct, 0)}</td>
      <td className="tabular py-1.5 text-right">{s.median_days_to_20 ?? "—"}</td>
    </>
  );
}

function TypeRows({ t }: { t: PerformanceType }) {
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <tr className="border-t border-border">
        <th scope="row" className="py-1.5 pr-3 text-left font-semibold">
          {t.label}
        </th>
        <Cells s={t.all} r={t.r} />
      </tr>
      {t.buckets.map((b) => (
        <tr key={b.bucket} className="text-muted">
          <th scope="row" className="py-1 pr-3 pl-4 text-left font-normal">
            Grade {b.bucket}
          </th>
          <Cells s={b} r={t.r} />
        </tr>
      ))}
      <tr>
        <td colSpan={9} className="pb-2 pl-4">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="text-xs text-tide-ink hover:underline"
          >
            {open ? "Hide" : "Show"} by market regime
          </button>
        </td>
      </tr>
      {open &&
        t.regimes.map((g) => (
          <tr key={g.regime} className="text-muted">
            <th scope="row" className="py-1 pr-3 pl-4 text-left font-normal">
              {regimeLabel(g.regime)}
            </th>
            <Cells s={g} r={t.r} />
          </tr>
        ))}
    </Fragment>
  );
}

export function PerformanceView() {
  const [horizon, setHorizon] = useState<number>(20);
  const [period, setPeriod] = useState<(typeof WINDOWS)[number]["key"]>("all");
  const months = WINDOWS.find((w) => w.key === period)?.months ?? null;
  const since = sinceFor(months);
  const data = useQuery({
    queryKey: ["performance", horizon, since],
    queryFn: () => api.get<Performance>(performancePath(horizon, since)),
  });
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Signal performance</h1>
        <p className="text-sm text-muted">
          How every signal the scan logged actually did, measured from the signal day&apos;s close.
          This is your own record, not a backtest: it grows every session.
        </p>
      </div>
      <div className="flex flex-wrap gap-4">
        <Toggle
          label="Period"
          value={period}
          options={WINDOWS.map((w) => ({ key: w.key, label: w.label }))}
          onChange={setPeriod}
        />
        <Toggle
          label="Measured after"
          value={horizon}
          options={HORIZONS.map((h) => ({ key: h, label: `${h} sessions` }))}
          onChange={setHorizon}
        />
      </div>
      {data.error && (
        <p role="alert" className="text-sm text-fall">
          {data.error.message}
        </p>
      )}
      {data.isPending && <div className="h-64 animate-pulse rounded bg-surface-2" />}
      {data.data && data.data.types.length === 0 && (
        <p className="text-sm text-muted">
          No signals in this period yet. They are logged by the nightly scan; check back after a few
          sessions.
        </p>
      )}
      {data.data && data.data.types.length > 0 && (
        <Section
          title="By signal and grade"
          note={`${data.data.first} to ${data.data.last} · ${formatNumber(data.data.total.signals)} signals`}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Signal performance {horizon} sessions after each signal, by type and grade
              </caption>
              <thead className="text-xs text-muted">
                <tr>
                  <th className="py-1 pr-3 text-left font-normal">Signal</th>
                  <th className="py-1 pr-3 text-right font-normal">Signals</th>
                  <th className="py-1 pr-3 text-right font-normal">Win rate</th>
                  <th className="py-1 pr-3 text-right font-normal">Average</th>
                  <th className="py-1 pr-3 text-right font-normal">Avg gain / loss</th>
                  <th className="py-1 pr-3 text-right font-normal">Expectancy</th>
                  <th className="py-1 pr-3 text-right font-normal">Stop hit</th>
                  <th className="py-1 pr-3 text-right font-normal">Reached +20%</th>
                  <th className="py-1 text-right font-normal">Median days to +20%</th>
                </tr>
              </thead>
              <tbody>
                {data.data.types.map((t) => (
                  <TypeRows key={t.type} t={t} />
                ))}
              </tbody>
            </table>
          </div>
          <ul className="flex list-disc flex-col gap-1 pl-4 text-xs text-muted">
            <li>
              Win rate and averages: the close {horizon} sessions after the signal against the
              signal day&apos;s close. Signals younger than that count as signals but not in the
              statistics.
            </li>
            <li>
              Expectancy is in R for breakouts only (they fire when the entry is reached): a stop
              hit within {horizon} sessions counts −1R, otherwise the move from the entry in units
              of the plan&apos;s risk.
            </li>
            <li>
              Stop hit: the day&apos;s low reached the plan&apos;s stop within {horizon} sessions.
              +20%: a high 20% above the signal close, within the 60 sessions tracked.
            </li>
          </ul>
        </Section>
      )}
    </div>
  );
}
