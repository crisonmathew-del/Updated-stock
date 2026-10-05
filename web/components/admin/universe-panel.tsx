"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type UniverseSummary } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { Panel, Stat } from "./panel";

export function UniversePanel() {
  const { data, error } = useQuery({
    queryKey: ["admin", "universe"],
    queryFn: () => api.get<UniverseSummary>("/api/admin/universe"),
    refetchInterval: 30_000,
  });

  const build = data?.last_build;
  return (
    <Panel
      title="Universe"
      description={
        build
          ? `Last rebuilt ${formatDateTime(build.started_at)} (${build.status}). Rebuilds every Sunday 18:00 ET.`
          : "Not built yet. Rebuild the universe to load the ticker list."
      }
    >
      {error && <p className="text-sm text-fail">{error.message}</p>}
      {data && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <Stat
            label="Stocks"
            value={formatNumber(data.stocks)}
            hint={`${formatNumber(data.by_type.common ?? 0)} common · ${formatNumber(data.by_type.adr ?? 0)} ADR`}
          />
          <Stat
            label="Benchmarks"
            value={formatNumber(data.benchmarks)}
            hint="Index ETFs, ^VIX, sectors"
          />
          <Stat
            label="With history"
            value={formatNumber(data.backfill.done)}
            hint={barRange(data)}
          />
          <Stat
            label="Not loaded"
            value={formatNumber(
              data.backfill.pending + data.backfill.failed + data.backfill.no_data,
            )}
            hint={`${data.backfill.pending} pending · ${data.backfill.failed} failed · ${data.backfill.no_data} no data`}
          />
          <Stat label="SEC company ID" value={formatNumber(data.with_cik)} />
          <Stat label="Industry code" value={formatNumber(data.with_sic)} />
          <Stat
            label="Market cap"
            value={formatNumber(data.with_market_cap)}
            hint="Common stock only"
          />
          <Stat label="Delisted (kept)" value={formatNumber(data.inactive)} />
        </dl>
      )}
    </Panel>
  );
}

function barRange(data: UniverseSummary): string | undefined {
  if (!data.earliest_bar || !data.latest_bar) return undefined;
  return `${data.earliest_bar} → ${data.latest_bar}`;
}
