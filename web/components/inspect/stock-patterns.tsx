"use client";

import { useQuery } from "@tanstack/react-query";
import { PatternCard } from "@/components/patterns/pattern-card";
import { api, type Pattern } from "@/lib/api";

export function StockPatterns({ symbol }: { symbol: string }) {
  const { data, error, isPending } = useQuery({
    queryKey: ["stock-patterns", symbol],
    queryFn: () => api.get<Pattern[]>(`/api/stocks/${encodeURIComponent(symbol)}/patterns`),
  });
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-sm font-medium">Patterns</h3>
      {isPending && <p className="text-sm text-muted">Loading patterns…</p>}
      {error && <p className="text-sm text-fail">{error.message}</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-muted">
          No bases or entry events detected for {symbol} yet. Detection runs after each EOD update;{" "}
          <code className="text-xs">make patterns symbols={symbol}</code> runs it now.
        </p>
      )}
      {data?.map((p, i) => (
        <PatternCard key={p.id} pattern={p} showChart={i === 0} />
      ))}
    </div>
  );
}
