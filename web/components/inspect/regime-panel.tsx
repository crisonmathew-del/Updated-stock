"use client";

import { useQuery } from "@tanstack/react-query";
import { Panel } from "@/components/admin/panel";
import { api, type Regime } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATE_STYLE: Record<string, { symbol: string; className: string; short: string }> = {
  confirmed_uptrend: { symbol: "▲", className: "text-ok", short: "Uptrend" },
  uptrend_under_pressure: { symbol: "!", className: "text-warn", short: "Pressure" },
  correction: { symbol: "▼", className: "text-fail", short: "Correction" },
};

function StateBadge({ state, label }: { state: string; label?: string }) {
  const style = STATE_STYLE[state] ?? { symbol: "?", className: "text-muted", short: state };
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap", style.className)}>
      <span aria-hidden>{style.symbol}</span>
      {label ?? style.short}
    </span>
  );
}

export function RegimePanel() {
  const { data, error } = useQuery({
    queryKey: ["market", "regime"],
    queryFn: () => api.get<Regime>("/api/market/regime?days=30"),
    refetchInterval: 60_000,
  });

  return (
    <Panel
      title="Market regime"
      description="SPY and QQQ (and IWM in small-cap mode); the market takes the weakest."
    >
      {error && <p className="text-sm text-fail">{error.message}</p>}
      {data && data.state == null && (
        <p className="text-sm text-muted">
          No regime yet. It is computed once index history is loaded and analytics run.
        </p>
      )}
      {data && data.state != null && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <p className="text-lg font-medium">
              <StateBadge state={data.state} label={data.label ?? undefined} />
            </p>
            <p className="text-xs text-muted">As of {data.date}</p>
          </div>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
            {data.reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr>
                  <th className="py-2 pr-4 font-normal">Index</th>
                  <th className="py-2 pr-4 font-normal">State</th>
                  <th className="py-2 pr-4 font-normal">Close</th>
                  <th className="py-2 pr-4 font-normal">21-day EMA</th>
                  <th className="py-2 pr-4 font-normal">50-day SMA</th>
                  <th className="py-2 pr-4 font-normal">Distribution days</th>
                  <th className="py-2 font-normal">Last follow-through</th>
                </tr>
              </thead>
              <tbody className="tabular divide-y divide-border">
                {data.indexes.map((index) => (
                  <tr key={index.symbol}>
                    <td className="py-2 pr-4 font-medium">{index.symbol}</td>
                    <td className="py-2 pr-4">
                      <StateBadge state={index.state} />
                      {index.rally_day != null && (
                        <span className="text-muted"> · rally day {index.rally_day}</span>
                      )}
                    </td>
                    <td className="py-2 pr-4">{formatPrice(index.close)}</td>
                    <td className="py-2 pr-4">{formatPrice(index.ema21)}</td>
                    <td className="py-2 pr-4">{formatPrice(index.sma50)}</td>
                    <td className="py-2 pr-4">{index.distribution_days}</td>
                    <td className="py-2">{index.last_ftd_date ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer text-muted">Last 30 sessions</summary>
            <ol className="mt-2 flex flex-col gap-1">
              {data.history.map((day) => (
                <li key={day.date} className="tabular flex gap-4">
                  <span className="w-24 text-muted">{day.date}</span>
                  <StateBadge state={day.states.MARKET ?? ""} />
                  {day.is_ftd && <span className="text-ok">follow-through day</span>}
                </li>
              ))}
            </ol>
          </details>
        </div>
      )}
    </Panel>
  );
}
