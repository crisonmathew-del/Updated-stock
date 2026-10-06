"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { api, type SetupDetail, type StockSummary } from "@/lib/api";
import { neighbours, useListStore } from "@/stores/list";
import { ChartPanel } from "./chart-panel";
import {
  FundamentalsPanel,
  InsidersPanel,
  NotesPanel,
  PatternPanel,
  PeersPanel,
  TrendTemplatePanel,
} from "./panels";
import { PlanCard, ScoreCard } from "./score-plan";
import { Section } from "@/components/ui/section";
import { StockHeader } from "./stock-header";
import { stockQueries } from "./queries";

export function summaryQuery(symbol: string) {
  return {
    queryKey: stockQueries(symbol).summary.key,
    queryFn: () => api.get<StockSummary>(stockQueries(symbol).summary.path),
    staleTime: 60_000,
  };
}

/** The stock page (spec §8.3): the chart is the hero; everything else supports it. */
export function StockPage({ symbol }: { symbol: string }) {
  const router = useRouter();
  const client = useQueryClient();
  const summary = useQuery(summaryQuery(symbol));
  const setup = useQuery({
    queryKey: stockQueries(symbol).setup.key,
    queryFn: () => api.get<SetupDetail | null>(stockQueries(symbol).setup.path),
    staleTime: 60_000,
  });
  const symbols = useListStore((s) => s.symbols);

  // Flip-through: have the previous and next stock ready (spec §10).
  useEffect(() => {
    for (const other of neighbours(symbols, symbol)) {
      if (!other) continue;
      router.prefetch(`/stocks/${other}`);
      void client.prefetchQuery(summaryQuery(other));
    }
  }, [symbols, symbol, router, client]);

  if (summary.error) {
    return (
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-10">
        <p className="text-fail">{summary.error.message}</p>
        <p className="mt-2 text-sm text-muted">Search for another ticker with ⌘K.</p>
      </main>
    );
  }
  const s = summary.data;
  const detail = setup.data;
  return (
    <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-4 px-4 py-4">
      {s ? (
        <StockHeader summary={s} setup={detail} />
      ) : (
        <div className="h-20 animate-pulse rounded-lg bg-surface" />
      )}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <ChartPanel symbol={symbol} />
        <div className="flex flex-col gap-4">
          {detail ? (
            <>
              <ScoreCard setup={detail} />
              <PlanCard key={detail.id} setup={detail} />
            </>
          ) : (
            <Section title="Setup">
              <p className="text-sm text-muted">
                {setup.isPending
                  ? "Loading…"
                  : "No setup: the stock isn't a trend leader and has no current base, so it isn't scored or planned."}
              </p>
            </Section>
          )}
        </div>
      </div>
      {s && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <TrendTemplatePanel summary={s} />
          <FundamentalsPanel symbol={symbol} />
          <PatternPanel setup={detail} />
          <PeersPanel symbol={symbol} summary={s} />
          <InsidersPanel symbol={symbol} />
          <NotesPanel symbol={symbol} />
        </div>
      )}
    </main>
  );
}
