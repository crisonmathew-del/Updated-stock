"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import { Section } from "@/components/ui/section";
import { StockLink } from "@/components/ui/stock-link";
import { api, type SetupList, type SignalEntry, type SignalList } from "@/lib/api";
import { formatPrice, formatReadiness } from "@/lib/format";
import { patternLabel } from "@/lib/stages";
import { cn } from "@/lib/utils";
import { shortDate } from "./market-panel";

const ROW =
  "grid items-center gap-x-3 rounded px-2 py-1.5 text-sm hover:bg-surface-2 focus-visible:bg-surface-2";

function Rows({ children, label }: { children: ReactNode; label: string }) {
  return (
    <ul aria-label={label} className="-mx-2 flex flex-col">
      {children}
    </ul>
  );
}

function Status({
  pending,
  error,
  empty,
  emptyText,
}: {
  pending: boolean;
  error: Error | null;
  empty: boolean;
  emptyText: ReactNode;
}) {
  if (pending) return <p className="text-sm text-muted">Loading…</p>;
  if (error) return <p className="text-sm text-fall">{error.message}</p>;
  if (empty) return <p className="text-sm text-muted">{emptyText}</p>;
  return null;
}

function Name({ symbol, name }: { symbol: string; name: string | null }) {
  return (
    <span className="min-w-0">
      <span className="font-medium">{symbol}</span>
      {name && <span className="block truncate text-xs text-muted">{name}</span>}
    </span>
  );
}

/** Actionable setups (basing, near pivot, just broken out), best Setup Score first. */
export function TopSetups({ className }: { className?: string }) {
  const q = useQuery({
    queryKey: ["setups", "dashboard", "top"],
    queryFn: () =>
      api.get<SetupList>("/api/setups?state=basing,near_pivot,breakout&sort=score&limit=10"),
    staleTime: 5 * 60_000,
  });
  const items = q.data?.items ?? [];
  const list = { source: "Top setups", symbols: items.map((s) => s.symbol) };
  return (
    <Section
      title="Top setups"
      note={<Link href="/admin/setups">All setups →</Link>}
      className={className}
    >
      <Status
        pending={q.isPending}
        error={q.error}
        empty={items.length === 0}
        emptyText="No active setups. They appear after the evening scan finds bases in leading stocks."
      />
      {items.length > 0 && (
        <Rows label="Top setups by score">
          {items.map((s) => (
            <li key={s.id}>
              <StockLink
                symbol={s.symbol}
                list={list}
                className={cn(ROW, "grid-cols-[minmax(0,1fr)_6.5rem_5.5rem_3.5rem]")}
              >
                <Name symbol={s.symbol} name={s.name} />
                <span className="truncate text-right text-xs text-muted">
                  {patternLabel(s.pattern_type)}
                </span>
                <StageBadge state={s.state} className="text-xs" />
                <GradeBadge grade={s.grade} score={s.score} />
              </StockLink>
            </li>
          ))}
        </Rows>
      )}
    </Section>
  );
}

/** Setups near their pivot, closest first: the names that could break out next. */
export function NearPivot({ className }: { className?: string }) {
  const q = useQuery({
    queryKey: ["setups", "dashboard", "near"],
    queryFn: () => api.get<SetupList>("/api/setups?state=near_pivot&sort=readiness&limit=10"),
    staleTime: 5 * 60_000,
  });
  const items = q.data?.items ?? [];
  const list = { source: "About to break out", symbols: items.map((s) => s.symbol) };
  return (
    <Section title="About to break out" note="Closest to the pivot" className={className}>
      <Status
        pending={q.isPending}
        error={q.error}
        empty={items.length === 0}
        emptyText="Nothing within reach of its pivot today."
      />
      {items.length > 0 && (
        <Rows label="Setups closest to their pivot">
          {items.map((s) => (
            <li key={s.id}>
              <StockLink
                symbol={s.symbol}
                list={list}
                className={cn(ROW, "grid-cols-[minmax(0,1fr)_6.5rem_5.5rem_3.5rem]")}
              >
                <Name symbol={s.symbol} name={s.name} />
                <span className="truncate text-right text-xs text-muted">
                  {patternLabel(s.pattern_type)}
                </span>
                <span className="tabular text-right text-xs">
                  <span className="text-tide-ink">{formatReadiness(s.readiness_pct)}</span>
                  <span className="block text-muted">pivot {formatPrice(s.pivot)}</span>
                </span>
                <GradeBadge grade={s.grade} score={s.score} />
              </StockLink>
            </li>
          ))}
        </Rows>
      )}
    </Section>
  );
}

/** The return since the signal at the longest horizon measured so far: [value, "5d"]. */
export function latestReturn(signal: SignalEntry): [number, string] | null {
  const returns = signal.outcome?.returns ?? {};
  const horizons = Object.keys(returns)
    .map(Number)
    .sort((a, b) => b - a);
  for (const h of horizons) {
    const value = returns[String(h)];
    if (value != null) return [value, `${h}d`];
  }
  return null;
}

/** Recent breakouts (confirmed at the close) and where each setup stands now. */
export function Breakouts({ className }: { className?: string }) {
  const q = useQuery({
    queryKey: ["signals", "dashboard", "breakouts"],
    queryFn: () => api.get<SignalList>("/api/signals?type=breakout&limit=10"),
    staleTime: 5 * 60_000,
  });
  const items = (q.data?.items ?? []).filter((s) => s.symbol);
  const latest = items[0]?.date;
  const today = items.filter((s) => s.date === latest).length;
  const list = { source: "Breakouts", symbols: items.map((s) => s.symbol as string) };
  return (
    <Section
      title="Breakouts"
      note={latest ? `${today} on ${shortDate(latest)}` : undefined}
      className={className}
    >
      <Status
        pending={q.isPending}
        error={q.error}
        empty={items.length === 0}
        emptyText="No breakouts logged yet. A breakout is a close above the pivot on heavy volume."
      />
      {items.length > 0 && (
        <Rows label="Recent breakouts">
          {items.map((s) => {
            const since = latestReturn(s);
            return (
              <li key={s.id}>
                <StockLink
                  symbol={s.symbol as string}
                  list={list}
                  className={cn(ROW, "grid-cols-[3.5rem_minmax(0,1fr)_6rem_5.5rem]")}
                >
                  <span className="text-xs text-muted">{shortDate(s.date)}</span>
                  <Name symbol={s.symbol as string} name={s.name} />
                  <span className="tabular text-right text-xs">
                    {formatPrice(s.price)}
                    <span className="block">
                      {since ? (
                        <>
                          <Change value={since[0]} digits={1} />{" "}
                          <span className="text-muted">{since[1]}</span>
                        </>
                      ) : (
                        <span className="text-muted">today</span>
                      )}
                    </span>
                  </span>
                  <StageBadge state={s.setup_state} className="text-xs" />
                </StockLink>
              </li>
            );
          })}
        </Rows>
      )}
    </Section>
  );
}

/** The latest signals of every kind, newest first (live in Phase 6). */
export function SignalFeed({ className }: { className?: string }) {
  const q = useQuery({
    queryKey: ["signals", "dashboard", "feed"],
    queryFn: () => api.get<SignalList>("/api/signals?limit=12"),
    staleTime: 5 * 60_000,
  });
  const items = q.data?.items ?? [];
  const list = {
    source: "Recent signals",
    symbols: [...new Set(items.flatMap((s) => (s.symbol ? [s.symbol] : [])))],
  };
  return (
    <Section
      title="Recent signals"
      note={<Link href="/admin/signals">Signal log →</Link>}
      className={className}
    >
      <Status
        pending={q.isPending}
        error={q.error}
        empty={items.length === 0}
        emptyText="No signals yet. Breakouts, pocket pivots and regime changes are logged here."
      />
      {items.length > 0 && (
        <Rows label="Recent signals">
          {items.map((s) => {
            const body = (
              <>
                <span className="text-xs text-muted">{shortDate(s.date)}</span>
                <span className="min-w-0">
                  <span className="font-medium">{s.symbol ?? "Market"}</span>{" "}
                  <span className="text-muted">· {s.type_label}</span>
                  <span className="block truncate text-xs text-muted" title={s.summary}>
                    {s.summary}
                  </span>
                </span>
              </>
            );
            const cls = cn(ROW, "grid-cols-[3.5rem_minmax(0,1fr)]");
            return (
              <li key={s.id}>
                {s.symbol ? (
                  <StockLink symbol={s.symbol} list={list} className={cls}>
                    {body}
                  </StockLink>
                ) : (
                  <Link href="/#market" className={cls}>
                    {body}
                  </Link>
                )}
              </li>
            );
          })}
        </Rows>
      )}
    </Section>
  );
}
