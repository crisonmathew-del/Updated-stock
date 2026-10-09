"use client";

import { useQuery } from "@tanstack/react-query";
import { Change } from "@/components/ui/badges";
import { Section } from "@/components/ui/section";
import { api, type BreadthDay, type IndexRegime, type Regime } from "@/lib/api";
import { formatNumber, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import { quoteLabel, quoteSession, useLive, useNewerQuote } from "@/stores/live";
import { DASHBOARD, DASHBOARD_STALE_MS } from "./queries";

export const REGIME_LOOK: Record<string, { icon: string; tone: string }> = {
  confirmed_uptrend: { icon: "▲", tone: "text-rise" },
  uptrend_under_pressure: { icon: "◆", tone: "text-warn" },
  correction: { icon: "▼", tone: "text-fall" },
};

const STATE_LABELS: Record<string, string> = {
  confirmed_uptrend: "Confirmed uptrend",
  uptrend_under_pressure: "Uptrend under pressure",
  correction: "Correction",
};

/** "2026-09-12" → "Sep 12". */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** The first session of the market's current state, from the newest-first history. */
export function stateSince(regime: Regime): string | null {
  let since: string | null = null;
  for (const day of regime.history) {
    if (day.states.MARKET !== regime.state) break;
    since = day.date;
  }
  return since;
}

function Figure({
  label,
  value,
  delta,
  hint,
}: {
  label: string;
  value: string;
  /** Change in percentage points against five sessions ago. */
  delta?: number | null;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tabular text-lg font-medium">{value}</dd>
      {delta !== undefined && (
        <dd className="text-xs text-muted">
          <Change value={delta} suffix=" pts" digits={0} /> in 5 sessions
        </dd>
      )}
      {hint && <dd className="text-xs text-muted">{hint}</dd>}
    </div>
  );
}

/** Net new highs per session as small bars around a zero line (newest on the right). */
function NetHighsBars({ days }: { days: BreadthDay[] }) {
  const series = [...days].reverse();
  const max = Math.max(1, ...series.map((d) => Math.abs(d.net_new_highs)));
  const width = 6;
  const gap = 2;
  const height = 36;
  const mid = height / 2;
  const first = series[0];
  const last = series[series.length - 1];
  return (
    <svg
      width={series.length * (width + gap)}
      height={height}
      role="img"
      aria-label={`Net new highs, last ${series.length} sessions: ${first.net_new_highs} on ${first.date} to ${last.net_new_highs} on ${last.date}.`}
    >
      <line x1={0} x2={series.length * (width + gap)} y1={mid} y2={mid} className="stroke-border" />
      {series.map((d, i) => {
        const h = Math.max(1, (Math.abs(d.net_new_highs) / max) * (mid - 1));
        const up = d.net_new_highs >= 0;
        return (
          <rect
            key={d.date}
            x={i * (width + gap)}
            y={up ? mid - h : mid}
            width={width}
            height={h}
            rx={1}
            className={up ? "fill-rise" : "fill-fall"}
          >
            <title>{`${d.date}: ${d.new_highs} new highs, ${d.new_lows} new lows (net ${d.net_new_highs > 0 ? "+" : ""}${d.net_new_highs})`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

function Breadth({ days }: { days: BreadthDay[] }) {
  if (days.length === 0) {
    return (
      <p className="text-sm text-muted">No breadth yet. It is computed by the evening scan.</p>
    );
  }
  const today = days[0];
  const before = days[5] ?? null;
  const diff = (a: number | null, b: number | null | undefined) =>
    a == null || b == null ? null : Math.round(a) - Math.round(b);
  const signed = (n: number) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${formatNumber(Math.abs(n))}`;
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Figure
          label="Above 50-day"
          value={today.pct_above_50 == null ? "—" : `${Math.round(today.pct_above_50)}%`}
          delta={diff(today.pct_above_50, before?.pct_above_50)}
        />
        <Figure
          label="Above 200-day"
          value={today.pct_above_200 == null ? "—" : `${Math.round(today.pct_above_200)}%`}
          delta={diff(today.pct_above_200, before?.pct_above_200)}
        />
        <Figure
          label="Net new highs"
          value={signed(today.net_new_highs)}
          hint={`${today.new_highs} highs · ${today.new_lows} lows`}
        />
        <Figure
          label="Advancers / decliners"
          value={`${formatNumber(today.advancers)} / ${formatNumber(today.decliners)}`}
        />
      </dl>
      <div className="flex items-center gap-3 text-xs text-muted">
        <NetHighsBars days={days} />
        <span>Net new highs, last {days.length} sessions</span>
      </div>
      <p className="text-xs text-muted">
        {formatNumber(today.members)} stocks in the universe on {shortDate(today.date)}.
      </p>
    </div>
  );
}

/** One index: its live price while the market is open (streamed), else the close. */
function IndexRow({ index: i, closeDate }: { index: IndexRegime; closeDate: string | null }) {
  const live = useNewerQuote(i.symbol, closeDate);
  const indexLook = REGIME_LOOK[i.state];
  return (
    <tr>
      <td className="py-1.5 pr-3">
        <span className="font-medium">{i.symbol}</span>{" "}
        <span className={cn("text-xs", indexLook?.tone ?? "text-muted")}>
          <span aria-hidden>{indexLook?.icon}</span> {i.label}
        </span>
      </td>
      <td
        className="py-1.5 pr-3 text-right whitespace-nowrap"
        title={
          live
            ? `${quoteLabel(live)} · close ${shortDate(closeDate)} ${formatPrice(i.close)}`
            : undefined
        }
      >
        {live ? (
          <>
            <span aria-hidden className="text-rise">
              ●
            </span>
            <span className="sr-only">{quoteLabel(live)}:</span> {formatPrice(live.last)}{" "}
            <Change value={live.change_pct} className="text-xs" />
          </>
        ) : (
          <>
            {formatPrice(i.close)} <Change value={i.change_pct} className="text-xs" />
          </>
        )}
      </td>
      <td
        className="py-1.5 pr-3 text-right"
        title={i.distribution_dates.length ? i.distribution_dates.join(", ") : undefined}
      >
        {i.distribution_days}
      </td>
      <td className="py-1.5 text-right whitespace-nowrap">{shortDate(i.last_ftd_date)}</td>
    </tr>
  );
}

/**
 * The market regime with its reasons (spec §8.2): the state and since when, each index's
 * distribution days and follow-through, and breadth (% above the 50/200-day, net new highs,
 * advancers/decliners). While the market is open the index prices are live; the regime itself
 * is judged at the close.
 */
export function MarketPanel({ className }: { className?: string }) {
  const regime = useQuery({
    queryKey: DASHBOARD.regime.key,
    queryFn: () => api.get<Regime>(DASHBOARD.regime.path),
    staleTime: DASHBOARD_STALE_MS,
  });
  const breadth = useQuery({
    queryKey: DASHBOARD.breadth.key,
    queryFn: () => api.get<BreadthDay[]>(DASHBOARD.breadth.path),
    staleTime: DASHBOARD_STALE_MS,
  });
  const r = regime.data;
  const anyLive = useLive((s) =>
    (r?.indexes ?? []).some((i) => {
      const day = quoteSession(s.quotes[i.symbol]);
      return !!day && (!r?.date || day > r.date);
    }),
  );
  const look = r?.state ? (REGIME_LOOK[r.state] ?? { icon: "•", tone: "text-muted" }) : null;
  const since = r ? stateSince(r) : null;

  return (
    <Section
      title="Market"
      anchor="market"
      note={r?.date ? `As of ${shortDate(r.date)}` : undefined}
      className={className}
    >
      {regime.isPending && <p className="text-sm text-muted">Loading the market regime…</p>}
      {regime.error && <p className="text-sm text-fall">{regime.error.message}</p>}
      {r && !r.state && (
        <p className="text-sm text-muted">
          No regime yet. It appears once index history is loaded and the evening scan has run.
        </p>
      )}
      {r?.state && look && (
        <>
          <div className="flex flex-col gap-1">
            <p className={cn("flex items-center gap-2 text-2xl font-semibold", look.tone)}>
              <span aria-hidden>{look.icon}</span>
              {r.label}
            </p>
            {since && (
              <p className="text-sm text-muted">
                Since {shortDate(since)}
                {r.changed_from && since === r.date
                  ? ` (was ${STATE_LABELS[r.changed_from] ?? r.changed_from})`
                  : ""}
              </p>
            )}
          </div>
          {r.reasons.length > 0 && (
            <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
              {r.reasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          )}
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Indexes behind the regime</caption>
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-1.5 pr-3 font-normal">Index</th>
                <th className="py-1.5 pr-3 text-right font-normal">
                  {anyLive ? "Price" : "Close"}
                </th>
                <th className="py-1.5 pr-3 text-right font-normal">Dist. days</th>
                <th className="py-1.5 text-right font-normal">Last FTD</th>
              </tr>
            </thead>
            <tbody className="tabular divide-y divide-border">
              {r.indexes.map((i) => (
                <IndexRow key={i.symbol} index={i} closeDate={r.date} />
              ))}
            </tbody>
          </table>
          {anyLive && (
            <p className="-mt-2 text-xs text-muted">
              <span aria-hidden className="text-rise">
                ●
              </span>{" "}
              Live prices; the regime and distribution days are judged at the close.
            </p>
          )}
        </>
      )}
      <div className="border-t border-border pt-3">
        <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Breadth</h3>
        {breadth.error && <p className="text-sm text-fall">{breadth.error.message}</p>}
        {breadth.data && <Breadth days={breadth.data} />}
      </div>
    </Section>
  );
}
