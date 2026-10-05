"use client";

import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Panel, Stat } from "@/components/admin/panel";
import { api, type StockSummary } from "@/lib/api";
import { formatChange, formatNumber, formatPrice, formatRankChange } from "@/lib/format";
import { cn } from "@/lib/utils";

const INDICATORS: {
  key: string;
  label: string;
  kind: "price" | "pct" | "ratio" | "number" | "flag";
}[] = [
  { key: "ema10", label: "10-day EMA", kind: "price" },
  { key: "ema21", label: "21-day EMA", kind: "price" },
  { key: "sma50", label: "50-day SMA", kind: "price" },
  { key: "sma150", label: "150-day SMA", kind: "price" },
  { key: "sma200", label: "200-day SMA", kind: "price" },
  { key: "high_52w", label: "52-week high", kind: "price" },
  { key: "low_52w", label: "52-week low", kind: "price" },
  { key: "atr14", label: "ATR (14)", kind: "price" },
  { key: "avg_volume_50", label: "Avg volume (50)", kind: "number" },
  { key: "volume_ratio", label: "Volume vs average", kind: "ratio" },
  { key: "up_down_volume_50", label: "Up/down volume (50)", kind: "ratio" },
  { key: "roc_63", label: "3-month change", kind: "pct" },
  { key: "roc_252", label: "12-month change", kind: "pct" },
  { key: "rs_slope_21", label: "RS line, 1 month", kind: "pct" },
  { key: "rs_new_high_ahead", label: "RS line new high ahead of price", kind: "flag" },
  { key: "bb_width", label: "Bollinger width", kind: "ratio" },
];

function formatIndicator(value: number | boolean | null | undefined, kind: string): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  switch (kind) {
    case "price":
      return formatPrice(value);
    case "pct":
      return formatChange(value);
    case "ratio":
      return value.toFixed(2);
    default:
      return formatNumber(Math.round(value));
  }
}

export function StockPanel({ initialSymbol = "" }: { initialSymbol?: string }) {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [draft, setDraft] = useState(initialSymbol);
  const { data, error, isFetching } = useQuery({
    queryKey: ["stock", symbol],
    queryFn: () => api.get<StockSummary>(`/api/stocks/${encodeURIComponent(symbol)}`),
    enabled: symbol.length > 0,
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSymbol(draft.trim().toUpperCase());
  }

  return (
    <Panel title="Stock" description="Trend Template, stage, RS and indicators for one ticker.">
      <form onSubmit={onSubmit} className="flex gap-2">
        <label className="sr-only" htmlFor="inspect-symbol">
          Ticker
        </label>
        <input
          id="inspect-symbol"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ticker, e.g. NVDA"
          className="w-48 rounded-md border border-border bg-background px-3 py-1.5 text-sm uppercase"
        />
        <button type="submit" className="rounded-md border border-border px-3 py-1.5 text-sm">
          Inspect
        </button>
      </form>

      {isFetching && <p className="text-sm text-muted">Loading {symbol}…</p>}
      {error && <p className="text-sm text-fail">{error.message}</p>}
      {data && !isFetching && (
        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-1">
            <p className="text-lg font-medium">
              {data.symbol} <span className="font-normal text-muted">· {data.name}</span>
            </p>
            <p className="text-sm text-muted">
              {[data.exchange, data.sector, data.industry].filter(Boolean).join(" · ")}
              {data.date && ` · as of ${data.date}`}
            </p>
          </div>

          {data.date == null ? (
            <p className="text-sm text-muted">
              No analytics for {data.symbol} yet. Load its history (backfill), then run analytics.
            </p>
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-5">
                <Stat label="Close" value={formatPrice(data.close)} />
                <Stat label="RS Rating" value={data.rs_rating ?? "—"} />
                <Stat label="Stage" value={data.stage_label ?? "—"} />
                <Stat
                  label="Trend Template"
                  value={`${data.trend_template_passed}/8`}
                  hint={data.trend_template_pass ? "Trend leader" : "Not a leader yet"}
                />
                <Stat
                  label="Group rank"
                  value={
                    data.group?.rank != null
                      ? `${data.group.rank} of ${data.group.ranked_groups}`
                      : "—"
                  }
                  hint={
                    data.group
                      ? `${data.group.name} · ${formatRankChange(data.group.rank_change_4w)} in 4 weeks`
                      : "No industry group"
                  }
                />
              </dl>

              <div className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">Trend Template</h3>
                <ul className="divide-y divide-border rounded-md border border-border">
                  {data.checks.map((check) => (
                    <li key={check.key} className="flex gap-3 px-3 py-2 text-sm">
                      <span
                        aria-hidden
                        className={cn("w-4 font-semibold", check.passed ? "text-ok" : "text-fail")}
                      >
                        {check.passed ? "✓" : "✕"}
                      </span>
                      <span className="flex-1">
                        <span className="sr-only">{check.passed ? "Pass: " : "Fail: "}</span>
                        {check.label}
                        <span className="tabular block text-xs text-muted">{check.detail}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                {INDICATORS.map(({ key, label, kind }) => (
                  <Stat
                    key={key}
                    label={label}
                    value={formatIndicator(data.indicators[key], kind)}
                  />
                ))}
              </dl>
            </>
          )}
        </div>
      )}
    </Panel>
  );
}
