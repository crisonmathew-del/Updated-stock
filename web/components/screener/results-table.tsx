"use client";

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { memo, useEffect, useMemo, useRef } from "react";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import { StockLink } from "@/components/ui/stock-link";
import {
  display,
  type Field,
  FIELDS,
  type Row,
  type Sort,
  sortValue,
  sparkPoints,
} from "@/lib/screener";
import { cn } from "@/lib/utils";

export const ROW_HEIGHT = 36;
const WATCH_WIDTH = 32;
const SPARK = { width: 52, height: 20 };

const Spark = memo(function Spark({ encoded }: { encoded: string }) {
  const points = sparkPoints(encoded, SPARK.width, SPARK.height);
  if (!points) return <span className="text-muted">—</span>;
  return (
    <svg width={SPARK.width} height={SPARK.height} aria-hidden className="text-muted">
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.25}
        strokeLinejoin="round"
      />
    </svg>
  );
});

function Cell({ field, row, list }: { field: Field; row: Row; list: () => List }) {
  switch (field) {
    case "symbol":
      return (
        <span className="min-w-0 leading-tight">
          <StockLink
            symbol={row.symbol}
            list={list}
            tabIndex={-1}
            className="font-medium hover:underline"
            onClick={(event) => event.stopPropagation()}
          >
            {row.symbol}
          </StockLink>
          <span className="block truncate text-xs text-muted" title={row.name}>
            {row.name}
          </span>
        </span>
      );
    case "spark":
      return <Spark encoded={row.spark} />;
    case "change_pct":
      return <Change value={row.change_pct} digits={1} />;
    case "grade":
      return row.grade || row.score != null ? (
        <GradeBadge grade={row.grade} score={row.score} />
      ) : (
        <span className="text-muted">—</span>
      );
    case "setup_state":
      return <StageBadge state={row.setup_state} />;
    default: {
      const text = display(field, row);
      return <span className={cn("truncate", text === "—" && "text-muted")}>{text}</span>;
    }
  }
}

/** The list a symbol link hands to `[` / `]`: the current results in their current order. */
type List = { source: string; symbols: string[] };

const numeric = (field: Field) => !["text", "enum", "spark"].includes(FIELDS[field].kind);

export type ResultsTableProps = {
  rows: Row[];
  columns: Field[];
  sort: Sort | null;
  onSort: (sort: Sort | null) => void;
  selected: string | null;
  onSelect: (symbol: string) => void;
  onWatch: (symbol: string) => void;
  /** Called with the rows in display order whenever they change (for j/k, CSV, `[`/`]`). */
  onOrder: (rows: Row[]) => void;
  source: string;
};

/**
 * The screener's results: a virtualised grid (only the visible rows are in the DOM, so 6,000
 * rows scroll smoothly), sorted client-side, missing values always last. Click a row to
 * preview it; the symbol opens the stock page; ＋ adds the stock to the watchlist.
 */
export function ResultsTable({
  rows,
  columns,
  sort,
  onSort,
  selected,
  onSelect,
  onWatch,
  onOrder,
  source,
}: ResultsTableProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const list = useRef<List>({ source, symbols: [] });
  const currentList = useMemo(() => () => list.current, []);

  const defs = useMemo<ColumnDef<Row>[]>(
    () =>
      columns.map((field) => {
        const def = FIELDS[field];
        const text = !numeric(field) && !def.sortValue;
        return {
          id: field,
          accessorFn: (row: Row) => sortValue(field, row) ?? undefined,
          header: def.short ?? def.label,
          size: def.width,
          enableSorting: def.kind !== "spark",
          sortDescFirst: def.descFirst ?? !text,
          sortUndefined: "last",
          sortingFn: text ? "text" : "basic",
        };
      }),
    [columns],
  );
  const sorting = useMemo<SortingState>(
    () => (sort && columns.includes(sort.field) ? [{ id: sort.field, desc: sort.desc }] : []),
    [sort, columns],
  );
  // TanStack Table returns functions that the React Compiler can't memoise safely.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns: defs,
    state: { sorting },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      onSort(next[0] ? { field: next[0].id as Field, desc: next[0].desc } : null);
    },
    enableSortingRemoval: false,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const ordered = table.getRowModel().rows;

  useEffect(() => {
    const original = ordered.map((r) => r.original);
    list.current = { source, symbols: original.map((r) => r.symbol) };
    onOrder(original);
  }, [ordered, source, onOrder]);

  const virtualizer = useVirtualizer({
    count: ordered.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
    initialRect: { width: 1200, height: 720 },
  });

  const selectedIndex = selected ? ordered.findIndex((r) => r.original.symbol === selected) : -1;
  useEffect(() => {
    if (selectedIndex >= 0) virtualizer.scrollToIndex(selectedIndex, { align: "auto" });
  }, [selectedIndex, virtualizer]);

  const template = [WATCH_WIDTH, ...columns.map((f) => FIELDS[f].width)]
    .map((w, i) => (i === 1 ? `minmax(${w}px, 1.5fr)` : `${w}px`))
    .join(" ");
  const width = WATCH_WIDTH + columns.reduce((n, f) => n + FIELDS[f].width, 0);

  return (
    <div
      ref={scroller}
      role="grid"
      aria-label="Screener results"
      aria-rowcount={ordered.length + 1}
      aria-colcount={columns.length + 1}
      aria-activedescendant={selected ? `screener-row-${selected}` : undefined}
      tabIndex={0}
      className="relative min-h-0 flex-1 overflow-auto rounded-lg border border-border bg-surface text-sm focus-visible:outline-offset-[-2px]"
    >
      <div style={{ minWidth: width }}>
        <div
          role="row"
          aria-rowindex={1}
          className="sticky top-0 z-10 grid border-b border-border bg-surface text-xs text-muted"
          style={{ gridTemplateColumns: template }}
        >
          <div role="columnheader" className="sticky left-0 bg-surface px-2 py-2">
            <span className="sr-only">Watch</span>
          </div>
          {table.getHeaderGroups()[0].headers.map((header) => {
            const field = header.id as Field;
            const sorted = header.column.getIsSorted();
            const right = numeric(field);
            return (
              <div
                key={header.id}
                role="columnheader"
                aria-sort={
                  sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"
                }
                className={cn(
                  "flex items-center px-2 py-2",
                  right && "justify-end",
                  field === "symbol" && "sticky left-8 bg-surface",
                )}
              >
                {header.column.getCanSort() ? (
                  <button
                    type="button"
                    title={FIELDS[field].description}
                    onClick={header.column.getToggleSortingHandler()}
                    className={cn(
                      "flex items-center gap-1 whitespace-nowrap hover:text-foreground",
                      sorted && "text-foreground",
                    )}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    <span aria-hidden className="w-2">
                      {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}
                    </span>
                  </button>
                ) : (
                  <span title={FIELDS[field].description}>
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </span>
                )}
              </div>
            );
          })}
        </div>
        <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
          {virtualizer.getVirtualItems().map((item) => {
            const row = ordered[item.index].original;
            const isSelected = row.symbol === selected;
            return (
              <div
                key={row.symbol}
                id={`screener-row-${row.symbol}`}
                role="row"
                aria-rowindex={item.index + 2}
                aria-selected={isSelected}
                onClick={() => onSelect(row.symbol)}
                className={cn(
                  "group absolute inset-x-0 top-0 grid cursor-pointer items-center border-b border-border/60",
                  isSelected ? "bg-surface-2" : "bg-surface hover:bg-surface-2",
                )}
                style={{
                  height: ROW_HEIGHT,
                  transform: `translateY(${item.start}px)`,
                  gridTemplateColumns: template,
                }}
              >
                <div
                  role="gridcell"
                  className={cn(
                    "sticky left-0 flex h-full items-center justify-center",
                    isSelected
                      ? "bg-surface-2 shadow-[inset_2px_0_0_var(--tide)]"
                      : "bg-surface group-hover:bg-surface-2",
                  )}
                >
                  <button
                    type="button"
                    tabIndex={-1}
                    aria-label={`Add ${row.symbol} to watchlist`}
                    title="Add to watchlist (w)"
                    onClick={(event) => {
                      event.stopPropagation();
                      onWatch(row.symbol);
                    }}
                    className="flex min-h-7 min-w-7 items-center justify-center rounded text-muted hover:bg-surface hover:text-foreground"
                  >
                    ＋
                  </button>
                </div>
                {columns.map((field) => (
                  <div
                    key={field}
                    role="gridcell"
                    className={cn(
                      "tabular flex min-w-0 items-center overflow-hidden px-2 whitespace-nowrap",
                      numeric(field) && "justify-end",
                      field === "symbol" && "sticky left-8 h-full",
                      field === "symbol" &&
                        (isSelected ? "bg-surface-2" : "bg-surface group-hover:bg-surface-2"),
                    )}
                  >
                    <Cell field={field} row={row} list={currentList} />
                  </div>
                ))}
              </div>
            );
          })}
        </div>
        {ordered.length === 0 && (
          <p className="px-4 py-10 text-center text-sm text-muted">
            No stocks match these filters. Loosen or remove one to see more.
          </p>
        )}
      </div>
    </div>
  );
}
