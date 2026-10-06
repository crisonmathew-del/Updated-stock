"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { api, type SavedScreen, type ScreenerSnapshot } from "@/lib/api";
import { isTyping, plainKey } from "@/lib/keys";
import {
  ALL_STOCKS,
  applyFilters,
  decode,
  DEFAULT_COLUMNS,
  type Field,
  FIELDS,
  PRESETS,
  type Row,
  type Screen,
  sameScreen,
  toCsv,
} from "@/lib/screener";
import { useAddToWatchlist } from "@/lib/use-watchlist";
import { cn } from "@/lib/utils";
import { useListStore } from "@/stores/list";
import { useToasts } from "@/stores/toast";
import { shortDate } from "@/components/dashboard/market-panel";
import { ColumnChooser } from "./column-chooser";
import { FilterBar } from "./filter-bar";
import { Preview } from "./preview";
import { ResultsTable } from "./results-table";

type Active = { kind: "preset"; id: string } | { kind: "saved"; id: number };

/** `?screen=12` (a saved screen), `?preset=vcps-near-pivot`, or the first preset. */
export function activeFrom(params: URLSearchParams): Active {
  const saved = Number(params.get("screen"));
  if (Number.isInteger(saved) && saved > 0) return { kind: "saved", id: saved };
  const preset = params.get("preset");
  if (preset && (preset === ALL_STOCKS.id || PRESETS.some((p) => p.id === preset))) {
    return { kind: "preset", id: preset };
  }
  return { kind: "preset", id: PRESETS[0].id };
}

const keyOf = (a: Active) => `${a.kind}:${a.id}`;

function fromSaved(s: SavedScreen): Screen {
  const columns = (s.columns ?? DEFAULT_COLUMNS).filter((c): c is Field => c in FIELDS);
  return {
    name: s.name,
    filters: s.filters,
    sort:
      s.sort && s.sort.field in FIELDS ? { field: s.sort.field as Field, desc: s.sort.desc } : null,
    columns: columns.includes("symbol") ? columns : ["symbol", ...columns],
  };
}

function download(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ScreenButton({
  active,
  name,
  count,
  title,
  onClick,
}: {
  active: boolean;
  name: string;
  count: number | null;
  title?: string;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        aria-current={active ? "true" : undefined}
        title={title}
        onClick={onClick}
        className={cn(
          "flex w-full items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm",
          active ? "bg-surface-2 text-foreground" : "text-muted hover:text-foreground",
        )}
      >
        <span className="truncate">{name}</span>
        {count != null && <span className="tabular text-xs text-muted">{count}</span>}
      </button>
    </li>
  );
}

/** Save a new screen (name prompt), save changes to the current one, or delete it. */
function SaveControls({
  active,
  screen,
  modified,
  onSaved,
  onDeleted,
}: {
  active: Active;
  screen: Screen;
  modified: boolean;
  onSaved: (saved: SavedScreen) => void;
  onDeleted: () => void;
}) {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [confirming, setConfirming] = useState(false);
  const body = { filters: screen.filters, sort: screen.sort, columns: screen.columns };
  const done = (saved: SavedScreen, verb: string) => {
    void client.invalidateQueries({ queryKey: ["screens"] });
    push(`${verb} "${saved.name}".`);
    onSaved(saved);
  };
  const create = useMutation({
    mutationFn: () => api.post<SavedScreen>("/api/screens", { name: name.trim(), ...body }),
    onSuccess: (saved) => {
      setNaming(false);
      setName("");
      done(saved, "Saved");
    },
    onError: (error) => push(`Couldn't save the screen: ${error.message}`, "error"),
  });
  const update = useMutation({
    mutationFn: (id: number) => api.patch<SavedScreen>(`/api/screens/${id}`, body),
    onSuccess: (saved) => done(saved, "Updated"),
    onError: (error) => push(`Couldn't save the changes: ${error.message}`, "error"),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/api/screens/${id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["screens"] });
      push(`Deleted "${screen.name}".`);
      setConfirming(false);
      onDeleted();
    },
    onError: (error) => push(`Couldn't delete the screen: ${error.message}`, "error"),
  });

  if (naming) {
    return (
      <form
        className="flex items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <input
          autoFocus
          aria-label="Screen name"
          placeholder="Name this screen"
          value={name}
          maxLength={80}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => event.key === "Escape" && setNaming(false)}
          className="h-7 w-48 rounded-md border border-border bg-background px-2 text-sm"
        />
        <Button
          size="sm"
          variant="primary"
          type="submit"
          disabled={!name.trim() || create.isPending}
        >
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setNaming(false)}>
          Cancel
        </Button>
      </form>
    );
  }
  return (
    <div className="flex items-center gap-2">
      {active.kind === "saved" && modified && (
        <Button
          size="sm"
          variant="primary"
          disabled={update.isPending}
          onClick={() => update.mutate(active.id)}
        >
          Save changes
        </Button>
      )}
      <Button
        size="sm"
        onClick={() => {
          setName(active.kind === "saved" ? `${screen.name} (copy)` : "");
          setNaming(true);
        }}
      >
        Save as new screen
      </Button>
      {active.kind === "saved" &&
        (confirming ? (
          <>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
            <Button size="sm" className="text-fall" onClick={() => remove.mutate(active.id)}>
              Delete &quot;{screen.name}&quot;
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
            Delete
          </Button>
        ))}
    </div>
  );
}

/**
 * The screener (spec §8.4): presets and saved screens on the left, the filter builder and a
 * virtualised results table in the middle, a chart preview of the selected row on the right.
 * Keys: j / k move through results, Enter opens the stock page, w adds to the watchlist, Esc
 * closes the preview.
 */
export function Screener() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const active = activeFrom(new URLSearchParams(params.toString()));
  const activeKey = keyOf(active);
  const add = useAddToWatchlist();

  const snapshot = useQuery({
    queryKey: ["screener"],
    queryFn: () => api.get<ScreenerSnapshot>("/api/screener"),
    staleTime: 5 * 60_000,
  });
  const saved = useQuery({
    queryKey: ["screens"],
    queryFn: () => api.get<SavedScreen[]>("/api/screens"),
  });
  const rows = useMemo(() => (snapshot.data ? decode(snapshot.data) : []), [snapshot.data]);

  // The source's own definition, and the user's unsaved edits to it (dropped on switching).
  const savedScreen =
    active.kind === "saved" ? saved.data?.find((s) => s.id === active.id) : undefined;
  const base: Screen | null =
    active.kind === "preset"
      ? (PRESETS.find((p) => p.id === active.id) ?? ALL_STOCKS)
      : savedScreen
        ? fromSaved(savedScreen)
        : null;
  const [edits, setEdits] = useState<{ key: string; screen: Screen } | null>(null);
  const screen = (edits?.key === activeKey ? edits.screen : null) ?? base ?? ALL_STOCKS;
  const modified = base != null && !sameScreen(screen, base);
  const edit = (patch: Partial<Screen>) =>
    setEdits({ key: activeKey, screen: { ...screen, ...patch } });

  const filters = useDeferredValue(screen.filters);
  const results = useMemo(() => applyFilters(rows, filters), [rows, filters]);
  const presetCounts = useMemo(
    () =>
      new Map([ALL_STOCKS, ...PRESETS].map((p) => [p.id, applyFilters(rows, p.filters).length])),
    [rows],
  );
  const savedCounts = useMemo(
    () => new Map((saved.data ?? []).map((s) => [s.id, applyFilters(rows, s.filters).length])),
    [rows, saved.data],
  );

  const [selected, setSelected] = useState<string | null>(null);
  const ordered = useRef<Row[]>([]);
  const onOrder = useCallback((r: Row[]) => {
    ordered.current = r;
  }, []);
  const list = useCallback(
    () => ({ source: screen.name, symbols: ordered.current.map((r) => r.symbol) }),
    [screen.name],
  );
  const selectedRow = selected ? (results.find((r) => r.symbol === selected) ?? null) : null;

  function go(next: Active) {
    setEdits(null);
    setSelected(null);
    const query = next.kind === "saved" ? `screen=${next.id}` : `preset=${next.id}`;
    router.replace(`${pathname}?${query}`, { scroll: false });
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTyping(event) || !plainKey(event) || event.defaultPrevented) return;
      if (document.querySelector("[data-radix-popper-content-wrapper]")) return; // a popover is open
      const rowsNow = ordered.current;
      const index = selected ? rowsNow.findIndex((r) => r.symbol === selected) : -1;
      if (event.key === "j" || event.key === "k") {
        event.preventDefault();
        const step = event.key === "j" ? 1 : -1;
        const next = rowsNow[Math.min(rowsNow.length - 1, Math.max(0, index + step))];
        if (next) setSelected(next.symbol);
      } else if (event.key === "Escape" && selected) {
        event.preventDefault();
        setSelected(null);
      } else if (event.key === "Enter" && selected) {
        const target = event.target as HTMLElement | null;
        if (target?.closest("a, button")) return;
        event.preventDefault();
        const { source, symbols } = list();
        useListStore.getState().setList(source, symbols);
        router.push(`/stocks/${selected}`);
      } else if (event.key === "w" && selected) {
        event.preventDefault();
        add.mutate(selected);
      }
    }
    // Capture phase, so the global j/k handler (Shortcuts) sees defaultPrevented and stands down.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [selected, list, router, add]);

  const optionsFor = useCallback(
    (field: Field) => {
      const fixed = FIELDS[field].options;
      if (fixed) return fixed;
      const values = new Set<string>();
      for (const r of rows) {
        const v = r[field];
        if (typeof v === "string" && v) values.add(v);
      }
      return [...values].sort().map((value) => ({ value, label: value }));
    },
    [rows],
  );

  const asOf = snapshot.data?.as_of;
  const presetDef = active.kind === "preset" ? PRESETS.find((p) => p.id === active.id) : null;
  const description =
    presetDef?.description ?? (active.kind === "preset" ? ALL_STOCKS.description : null);

  return (
    <main className="mx-auto flex h-[calc(100dvh-7rem)] min-h-[36rem] w-full max-w-[1920px] gap-4 px-4 py-4 sm:h-[calc(100dvh-3.0625rem)]">
      <nav
        aria-label="Screens"
        className="hidden w-56 shrink-0 flex-col gap-4 overflow-y-auto lg:flex"
      >
        <div>
          <h2 className="mb-1 px-2 text-xs font-semibold tracking-wide text-muted uppercase">
            Presets
          </h2>
          <ul>
            {PRESETS.map((p) => (
              <ScreenButton
                key={p.id}
                name={p.name}
                title={p.description}
                count={snapshot.data ? (presetCounts.get(p.id) ?? 0) : null}
                active={active.kind === "preset" && active.id === p.id}
                onClick={() => go({ kind: "preset", id: p.id })}
              />
            ))}
          </ul>
        </div>
        <div>
          <h2 className="mb-1 px-2 text-xs font-semibold tracking-wide text-muted uppercase">
            My screens
          </h2>
          <ul>
            {(saved.data ?? []).map((s) => (
              <ScreenButton
                key={s.id}
                name={s.name}
                count={snapshot.data ? (savedCounts.get(s.id) ?? 0) : null}
                active={active.kind === "saved" && active.id === s.id}
                onClick={() => go({ kind: "saved", id: s.id })}
              />
            ))}
            <ScreenButton
              name="＋ New screen"
              title={ALL_STOCKS.description}
              count={null}
              active={active.kind === "preset" && active.id === ALL_STOCKS.id}
              onClick={() => go({ kind: "preset", id: ALL_STOCKS.id })}
            />
          </ul>
          {saved.data?.length === 0 && (
            <p className="px-2 pt-1 text-xs text-muted">
              Change a preset&apos;s filters, then &ldquo;Save as new screen&rdquo; to keep it here.
            </p>
          )}
        </div>
      </nav>

      <section aria-labelledby="screen-title" className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            <label className="mb-1 block lg:hidden">
              <span className="sr-only">Screen</span>
              <select
                value={activeKey}
                onChange={(event) => {
                  const [kind, id] = event.target.value.split(":");
                  go(kind === "saved" ? { kind: "saved", id: Number(id) } : { kind: "preset", id });
                }}
                className="h-8 rounded-md border border-border bg-surface px-2 text-sm"
              >
                {[...PRESETS, ALL_STOCKS].map((p) => (
                  <option key={p.id} value={`preset:${p.id}`}>
                    {p.name}
                  </option>
                ))}
                {(saved.data ?? []).map((s) => (
                  <option key={s.id} value={`saved:${s.id}`}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <h1 id="screen-title" className="text-lg font-semibold">
              {screen.name}
              {modified && <span className="ml-2 text-sm font-normal text-muted">· edited</span>}
            </h1>
            <p className="text-sm text-muted">
              {snapshot.data ? (
                <>
                  <span className="tabular text-foreground">
                    {results.length.toLocaleString("en-US")}
                  </span>{" "}
                  of {rows.length.toLocaleString("en-US")} stocks
                  {asOf && ` · as of ${shortDate(asOf)} close`}
                </>
              ) : (
                "Loading the latest session…"
              )}
              {description && ` · ${description}`}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SaveControls
              active={active}
              screen={screen}
              modified={modified}
              onSaved={(s) => go({ kind: "saved", id: s.id })}
              onDeleted={() => go({ kind: "preset", id: ALL_STOCKS.id })}
            />
            <ColumnChooser columns={screen.columns} onChange={(columns) => edit({ columns })} />
            <Button
              size="sm"
              disabled={results.length === 0}
              onClick={() =>
                download(
                  `breakout-${screen.name.toLowerCase().replace(/\W+/g, "-")}-${asOf ?? "latest"}.csv`,
                  toCsv(ordered.current, screen.columns),
                )
              }
            >
              Export CSV
            </Button>
          </div>
        </div>
        <FilterBar
          filters={screen.filters}
          onChange={(f) => edit({ filters: f })}
          optionsFor={optionsFor}
        />
        {snapshot.error && <p className="text-sm text-fall">{snapshot.error.message}</p>}
        {active.kind === "saved" && saved.data && !base && (
          <p className="text-sm text-fall">That saved screen no longer exists. Pick another one.</p>
        )}
        {snapshot.data && rows.length === 0 && (
          <p className="text-sm text-muted">
            No stocks to screen yet. Load prices (<code>make backfill</code>) and run the evening
            scan (<code>make scan-now</code>).
          </p>
        )}
        {snapshot.isPending ? (
          <div className="flex-1 animate-pulse rounded-lg border border-border bg-surface" />
        ) : (
          <ResultsTable
            rows={results}
            columns={screen.columns}
            sort={screen.sort}
            onSort={(sort) => edit({ sort })}
            selected={selected}
            onSelect={setSelected}
            onWatch={(symbol) => add.mutate(symbol)}
            onOrder={onOrder}
            source={screen.name}
          />
        )}
        <p className="hidden text-xs text-muted md:block">
          Click a row to preview it · j / k move · Enter opens the stock page · w adds to the
          watchlist · Esc closes the preview
        </p>
      </section>

      {selectedRow && (
        <Preview
          row={selectedRow}
          list={list}
          onClose={() => setSelected(null)}
          onWatch={(symbol) => add.mutate(symbol)}
        />
      )}
    </main>
  );
}
