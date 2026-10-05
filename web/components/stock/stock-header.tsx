"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import { Kbd } from "@/components/ui/kbd";
import {
  api,
  type Membership,
  type SetupDetail,
  type StockSummary,
  type Watchlist,
} from "@/lib/api";
import { formatCompact, formatPrice, formatRankChange } from "@/lib/format";
import { useAddToWatchlist } from "@/lib/use-watchlist";
import { neighbours, useListStore } from "@/stores/list";

function WatchButton({ symbol }: { symbol: string }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const add = useAddToWatchlist();
  const lists = useQuery({
    queryKey: ["membership", symbol],
    queryFn: () => api.get<Membership[]>(`/api/stocks/${symbol}/watchlists`),
  });
  const toggle = useMutation({
    mutationFn: async (m: Membership): Promise<void> => {
      if (m.contains) await api.delete<Watchlist>(`/api/watchlists/${m.id}/items/${symbol}`);
      else await api.post<Watchlist>(`/api/watchlists/${m.id}/items`, { symbol });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["membership", symbol] });
      void client.invalidateQueries({ queryKey: ["watchlists"] });
    },
  });
  const watched = lists.data?.some((m) => m.contains) ?? false;
  const noLists = lists.data?.length === 0;
  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => (noLists ? add.mutate(symbol) : setOpen(!open))}
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-xs hover:border-muted"
      >
        <span aria-hidden className={watched ? "text-tide" : "text-muted"}>
          {watched ? "★" : "☆"}
        </span>
        {watched ? "On watchlist" : "Add to watchlist"}
        <Kbd>W</Kbd>
      </button>
      {open && lists.data && (
        <ul
          className="absolute z-30 mt-1 w-56 rounded-md border border-border bg-surface p-1 text-sm shadow-lg"
          onMouseLeave={() => setOpen(false)}
        >
          {lists.data.map((m) => (
            <li key={m.id}>
              <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-surface-2">
                <input
                  type="checkbox"
                  checked={m.contains}
                  disabled={toggle.isPending}
                  onChange={() => toggle.mutate(m)}
                />
                {m.name}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Ticker, price, change, volume, size, group rank, earnings countdown, grade and stage. */
export function StockHeader({
  summary,
  setup,
}: {
  summary: StockSummary;
  setup: SetupDetail | null | undefined;
}) {
  const symbols = useListStore((s) => s.symbols);
  const source = useListStore((s) => s.source);
  const [previous, next] = neighbours(symbols, summary.symbol);
  const g = summary.group;
  const facts = [
    summary.volume_ratio != null && `Vol ${summary.volume_ratio.toFixed(1)}× avg`,
    summary.market_cap != null && `Cap ${formatCompact(summary.market_cap)}`,
    g &&
      `${g.name} rank ${g.rank ?? "–"} of ${g.ranked_groups ?? "–"}${g.rank_change_4w ? ` (${formatRankChange(g.rank_change_4w)} in 4 weeks)` : ""}`,
    summary.next_earnings &&
      `Earnings ≈ ${summary.next_earnings}${summary.sessions_to_earnings != null ? ` (${summary.sessions_to_earnings} sessions)` : ""}`,
  ].filter(Boolean) as string[];
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{summary.symbol}</h1>
          <span className="truncate text-muted">{summary.name}</span>
          <WatchButton symbol={summary.symbol} />
          {source && (previous || next) && (
            <span className="flex items-center gap-1 text-xs text-muted">
              {previous ? (
                <Link
                  href={`/stocks/${previous}`}
                  className="hover:text-foreground"
                  title={`Previous in ${source}`}
                >
                  ‹ {previous}
                </Link>
              ) : null}
              <Kbd>[</Kbd>
              <Kbd>]</Kbd>
              {next ? (
                <Link
                  href={`/stocks/${next}`}
                  className="hover:text-foreground"
                  title={`Next in ${source}`}
                >
                  {next} ›
                </Link>
              ) : null}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="tabular text-2xl">{formatPrice(summary.close)}</span>
          <Change value={summary.change} suffix="" />
          <Change value={summary.change_pct} />
          <span className="text-sm text-muted">{summary.date}</span>
        </div>
        <p className="text-sm text-muted">{facts.join(" · ")}</p>
      </div>
      <div className="flex flex-col items-end gap-1.5">
        <GradeBadge grade={setup?.grade} score={setup?.score} size="lg" />
        {setup ? (
          <span className="text-sm">
            <StageBadge state={setup.state} />{" "}
            <span className="text-muted">since {setup.state_since}</span>
          </span>
        ) : (
          <span className="text-sm text-muted">No setup: not a trend leader and no base</span>
        )}
      </div>
    </header>
  );
}
