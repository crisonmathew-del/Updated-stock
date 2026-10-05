"use client";

import { ChartPanel } from "@/components/stock/chart-panel";
import { Button, buttonStyles } from "@/components/ui/button";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import { Kbd } from "@/components/ui/kbd";
import { StockLink } from "@/components/ui/stock-link";
import { formatPrice } from "@/lib/format";
import { display, type Field, FIELDS, type Row } from "@/lib/screener";

const FACTS: Field[] = [
  "rs_rating",
  "tt_passed",
  "stage",
  "group_rank",
  "fund_grade",
  "pattern",
  "pivot",
  "readiness_pct",
  "off_high_pct",
  "vs_sma50_pct",
  "volume_ratio",
  "market_cap",
];

/**
 * The screener's side panel: a compact chart (same overlays as the stock page) and the row's
 * key numbers, without leaving the results. Enter opens the full stock page, Esc closes.
 */
export function Preview({
  row,
  list,
  onClose,
  onWatch,
}: {
  row: Row;
  list: () => { source: string; symbols: string[] };
  onClose: () => void;
  onWatch: (symbol: string) => void;
}) {
  return (
    <aside
      aria-label={`${row.symbol} preview`}
      className="fixed inset-y-0 right-0 z-30 flex w-full max-w-md flex-col gap-3 overflow-y-auto border-l border-border bg-background p-4 shadow-xl xl:static xl:z-auto xl:w-[30rem] xl:max-w-none xl:shrink-0 xl:rounded-lg xl:border xl:bg-surface xl:shadow-none"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">
            {row.symbol} <span className="tabular font-normal">{formatPrice(row.close)}</span>{" "}
            <Change value={row.change_pct} className="text-sm" />
          </h2>
          <p className="truncate text-sm text-muted">
            {row.name}
            {row.group ? ` · ${row.group}` : ""}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close preview"
          title="Close (Esc)"
          onClick={onClose}
          className="rounded px-2 py-0.5 text-muted hover:bg-surface-2 hover:text-foreground"
        >
          ×
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <GradeBadge grade={row.grade} score={row.score} />
        <StageBadge state={row.setup_state} />
        {row.tt_pass && <span className="text-rise">✓ Trend Template</span>}
      </div>
      <ChartPanel symbol={row.symbol} height={300} compact />
      <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-sm">
        {FACTS.map((f) => (
          <div key={f} className="min-w-0">
            <dt className="truncate text-xs text-muted" title={FIELDS[f].description}>
              {FIELDS[f].label}
            </dt>
            <dd className="tabular truncate">{display(f, row)}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap gap-2">
        <StockLink
          symbol={row.symbol}
          list={list}
          className={buttonStyles({ variant: "primary", size: "sm" })}
        >
          Open stock page <Kbd>↵</Kbd>
        </StockLink>
        <Button size="sm" onClick={() => onWatch(row.symbol)}>
          Add to watchlist <Kbd>w</Kbd>
        </Button>
      </div>
    </aside>
  );
}
