"use client";

import { useQuery } from "@tanstack/react-query";
import { Section } from "@/components/ui/section";
import { api, type Groups, type SectorRow } from "@/lib/api";
import { formatChange } from "@/lib/format";
import { cn } from "@/lib/utils";
import { shortDate } from "./market-panel";
import { DASHBOARD, DASHBOARD_STALE_MS } from "./queries";

/** A rank change over 4 weeks with an arrow, so it never relies on colour: ▲ 3, ▼ 2, –. */
export function RankTrend({ change }: { change: number | null }) {
  if (change == null) return <span className="text-muted">—</span>;
  const up = change > 0;
  const down = change < 0;
  return (
    <span
      role="img"
      className={cn(
        "tabular whitespace-nowrap",
        up ? "text-rise" : down ? "text-fall" : "text-muted",
      )}
      aria-label={up ? `up ${change} places` : down ? `down ${-change} places` : "unchanged"}
    >
      <span aria-hidden>{up ? "▲" : down ? "▼" : "–"}</span>
      {change !== 0 && ` ${Math.abs(change)}`}
    </span>
  );
}

const useGroups = () =>
  useQuery({
    queryKey: DASHBOARD.groups.key,
    queryFn: () => api.get<Groups>(DASHBOARD.groups.path),
    staleTime: DASHBOARD_STALE_MS,
  });

/** The top 10 industry groups by rank, with their 4-week rank trend. */
export function LeadingGroups({ className }: { className?: string }) {
  const q = useGroups();
  const groups = q.data?.groups ?? [];
  return (
    <Section
      title="Leading groups"
      note={q.data?.date ? `Ranked ${shortDate(q.data.date)} · trend over 4 weeks` : undefined}
      className={className}
    >
      {q.isPending && <p className="text-sm text-muted">Loading…</p>}
      {q.error && <p className="text-sm text-fall">{q.error.message}</p>}
      {q.data && groups.length === 0 && (
        <p className="text-sm text-muted">
          No ranked groups yet. Groups come from SEC industry codes (set SEC_USER_AGENT) and need 3
          or more stocks each.
        </p>
      )}
      {groups.length > 0 && (
        <table className="w-full table-fixed text-left text-sm">
          <colgroup>
            <col className="w-7" />
            <col className="w-10" />
            <col />
            <col className="w-12" />
            <col className="w-16" />
            <col className="w-14" />
          </colgroup>
          <thead className="text-xs whitespace-nowrap text-muted">
            <tr>
              <th className="py-1.5 pr-2 font-normal">#</th>
              <th className="py-1.5 pr-2 font-normal">4 wk</th>
              <th className="py-1.5 pr-2 font-normal">Group</th>
              <th className="py-1.5 pr-2 text-right font-normal" title="Median RS Rating">
                RS
              </th>
              <th className="py-1.5 pr-2 text-right font-normal">3 mo</th>
              <th
                className="py-1.5 text-right font-normal"
                title="Stocks passing the Trend Template"
              >
                Leaders
              </th>
            </tr>
          </thead>
          <tbody className="tabular divide-y divide-border">
            {groups.map((g) => (
              <tr key={g.group_id}>
                <td className="py-1.5 pr-2 font-medium">{g.rank}</td>
                <td className="py-1.5 pr-2">
                  <RankTrend change={g.rank_change_4w} />
                </td>
                <td className="py-1.5 pr-2">
                  <span className="block truncate" title={g.name}>
                    {g.name}
                  </span>
                  <span className="block truncate text-xs text-muted">{g.sector}</span>
                </td>
                <td className="py-1.5 pr-2 text-right">{g.median_rs ?? "—"}</td>
                <td className="py-1.5 pr-2 text-right">{formatChange(g.return_3m)}</td>
                <td className="py-1.5 text-right">
                  {g.tt_passing}
                  <span className="text-muted">/{g.members}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

/** Where zero sits across the bar track and each bar's span, as % of the track width. */
export function divergingLayout(values: (number | null)[]): {
  zero: number;
  bar: (value: number | null) => { left: number; width: number };
} {
  const finite = values.filter((v): v is number => v != null);
  const pos = Math.max(0, ...finite);
  const neg = Math.max(0, ...finite.map((v) => -v));
  const span = pos + neg || 1;
  const zero = (neg / span) * 100;
  return {
    zero,
    bar: (value) => {
      if (value == null) return { left: zero, width: 0 };
      const width = (Math.abs(value) / span) * 100;
      return { left: value >= 0 ? zero : zero - width, width };
    },
  };
}

function RotationRow({
  sector,
  zero,
  bar,
}: {
  sector: SectorRow;
  zero: number;
  bar: { left: number; width: number };
}) {
  const up = (sector.return_3m ?? 0) >= 0;
  return (
    <tr className="group">
      <td className="py-1 pr-2 text-muted">{sector.rank}</td>
      <td className="py-1 pr-2 whitespace-nowrap">
        {sector.sector} <span className="text-xs text-muted">{sector.symbol}</span>
      </td>
      <td className="w-full py-1 pr-2" aria-hidden>
        <div className="relative h-3 rounded-sm group-hover:bg-surface-2">
          <div className="absolute -inset-y-1 w-px bg-border" style={{ left: `${zero}%` }} />
          <div
            className={cn("absolute inset-y-0.5", up ? "bg-rise" : "bg-fall")}
            style={{
              left: `${bar.left}%`,
              width: `${bar.width}%`,
              // Rounded on the data end only; the base stays square against the zero line.
              borderRadius: up ? "0 3px 3px 0" : "3px 0 0 3px",
            }}
          />
        </div>
      </td>
      <td className="tabular py-1 pr-2 text-right whitespace-nowrap">
        {formatChange(sector.return_3m)}
      </td>
      <td className="py-1 text-right">
        <RankTrend change={sector.rank_change_4w} />
      </td>
    </tr>
  );
}

/**
 * Sector rotation: the 11 sector ETFs ranked by relative strength, with each one's 3-month
 * return as a bar around zero and its rank change over 4 weeks.
 */
export function SectorRotation({ className }: { className?: string }) {
  const q = useGroups();
  const sectors = q.data?.sectors ?? [];
  const layout = divergingLayout(sectors.map((s) => s.return_3m));
  return (
    <Section
      title="Sector rotation"
      note="Ranked by RS · 3-month return · 4-week trend"
      className={className}
    >
      {q.isPending && <p className="text-sm text-muted">Loading…</p>}
      {q.error && <p className="text-sm text-fall">{q.error.message}</p>}
      {q.data && sectors.length === 0 && (
        <p className="text-sm text-muted">
          No sector ETF history yet. It loads with the prices (progress on Admin → Data; by hand:
          <code>make backfill</code>).
        </p>
      )}
      {sectors.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-xs whitespace-nowrap text-muted">
            <tr>
              <th className="py-1 pr-2 font-normal">#</th>
              <th className="py-1 pr-2 font-normal">Sector</th>
              <th className="py-1 pr-2 font-normal">
                <span className="sr-only">3-month return bar</span>
              </th>
              <th className="py-1 pr-2 text-right font-normal">3 mo</th>
              <th className="py-1 text-right font-normal">4 wk</th>
            </tr>
          </thead>
          <tbody>
            {sectors.map((s) => (
              <RotationRow
                key={s.symbol}
                sector={s}
                zero={layout.zero}
                bar={layout.bar(s.return_3m)}
              />
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}
