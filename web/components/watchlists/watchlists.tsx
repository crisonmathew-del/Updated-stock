"use client";

import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { StockLink } from "@/components/ui/stock-link";
import { api, type Watchlist, type WatchlistItem } from "@/lib/api";
import { formatPrice, formatReadiness } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useListStore } from "@/stores/list";
import { useToasts } from "@/stores/toast";

const KEY = ["watchlists"];

/** Columns: handle, symbol, price, change, RS, setup, stage, to pivot, note, remove. Phones keep
 * handle, symbol, price, change and remove, with the note on its own line below. */
const GRID =
  "grid grid-cols-[1.5rem_minmax(0,1fr)_4.5rem_5.5rem_2rem] md:grid-cols-[1.5rem_minmax(8rem,1.2fr)_5rem_5.5rem_2.5rem_4.5rem_6.5rem_6.5rem_minmax(10rem,2fr)_2rem] items-center gap-x-2";
const WIDE = "hidden md:block";

function useReplaceList() {
  const client = useQueryClient();
  return (updated: Watchlist) =>
    client.setQueryData<Watchlist[]>(KEY, (lists) =>
      lists?.map((l) => (l.id === updated.id ? updated : l)),
    );
}

/** A note that saves when you leave the field (or press Enter); Esc puts it back. */
function NoteField({
  symbol,
  value,
  onSave,
}: {
  symbol: string;
  value: string | null;
  onSave: (note: string | null) => void;
}) {
  const [text, setText] = useState(value ?? "");
  return (
    <input
      aria-label={`Note for ${symbol}`}
      placeholder="Add a note…"
      value={text}
      maxLength={2000}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => {
        const next = text.trim() || null;
        if (next !== (value ?? null)) onSave(next);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") {
          setText(value ?? "");
          event.currentTarget.blur();
        }
      }}
      className="h-7 w-full min-w-0 rounded border border-transparent bg-transparent px-1.5 text-sm placeholder:text-muted/70 hover:border-border focus:border-border focus:bg-background"
    />
  );
}

function ItemRow({
  item,
  list,
  onNote,
  onRemove,
}: {
  item: WatchlistItem;
  list: { source: string; symbols: string[] };
  onNote: (note: string | null) => void;
  onRemove: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.symbol });
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        GRID,
        "border-b border-border/60 px-2 py-1.5 text-sm",
        isDragging ? "relative z-10 bg-surface-2 shadow-lg" : "bg-surface hover:bg-surface-2/60",
      )}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-label={`Move ${item.symbol}`}
        title="Drag to reorder (or focus and press Space, then the arrow keys)"
        className="cursor-grab touch-none rounded text-muted hover:text-foreground active:cursor-grabbing"
      >
        ⋮⋮
      </button>
      <StockLink symbol={item.symbol} list={list} className="min-w-0 leading-tight">
        <span className="font-medium">{item.symbol}</span>
        <span className="block truncate text-xs text-muted">{item.name}</span>
      </StockLink>
      <span className="tabular text-right">{formatPrice(item.close)}</span>
      <span className="text-right">
        <Change value={item.change_pct} />
      </span>
      <span className={cn(WIDE, "tabular text-right")}>{item.rs_rating ?? "—"}</span>
      <span className={WIDE}>
        {item.grade || item.score != null ? (
          <GradeBadge grade={item.grade} score={item.score} />
        ) : (
          <span className="text-muted">—</span>
        )}
      </span>
      <span className={cn(WIDE, "truncate text-xs")}>
        <StageBadge state={item.state} />
      </span>
      <span className={cn(WIDE, "tabular truncate text-right text-xs")}>
        {item.readiness_pct == null ? "—" : formatReadiness(item.readiness_pct)}
      </span>
      <div className="order-last col-span-4 col-start-2 md:order-none md:col-span-1 md:col-start-auto">
        <NoteField key={item.note ?? ""} symbol={item.symbol} value={item.note} onSave={onNote} />
      </div>
      <button
        type="button"
        aria-label={`Remove ${item.symbol} from the list`}
        title="Remove from this list"
        onClick={onRemove}
        className="rounded text-muted hover:bg-surface hover:text-fall"
      >
        ×
      </button>
    </li>
  );
}

function AddStock({ watchlist }: { watchlist: Watchlist }) {
  const replaceList = useReplaceList();
  const push = useToasts((s) => s.push);
  const [symbol, setSymbol] = useState("");
  const add = useMutation({
    mutationFn: (s: string) =>
      api.post<Watchlist>(`/api/watchlists/${watchlist.id}/items`, { symbol: s }),
    onSuccess: (updated, s) => {
      replaceList(updated);
      setSymbol("");
      push(`Added ${s.toUpperCase()} to ${updated.name}.`);
    },
  });
  return (
    <form
      className="flex flex-col gap-1"
      onSubmit={(event) => {
        event.preventDefault();
        if (symbol.trim()) add.mutate(symbol.trim());
      }}
    >
      <div className="flex items-center gap-2">
        <input
          aria-label="Symbol to add"
          placeholder="Add a symbol, e.g. NVDA"
          value={symbol}
          maxLength={16}
          onChange={(event) => setSymbol(event.target.value.toUpperCase())}
          className="h-8 w-48 rounded-md border border-border bg-background px-2 text-sm uppercase placeholder:normal-case"
        />
        <Button size="sm" type="submit" disabled={!symbol.trim() || add.isPending}>
          Add
        </Button>
      </div>
      {add.error && (
        <p role="alert" className="text-xs text-fall">
          {add.error.message}
        </p>
      )}
    </form>
  );
}

/** The list's name (click to rename), its size, flip-through and delete. */
function ListHeader({ watchlist, onDeleted }: { watchlist: Watchlist; onDeleted: () => void }) {
  const client = useQueryClient();
  const replaceList = useReplaceList();
  const router = useRouter();
  const push = useToasts((s) => s.push);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(watchlist.name);
  const [confirming, setConfirming] = useState(false);
  const rename = useMutation({
    mutationFn: () =>
      api.patch<Watchlist>(`/api/watchlists/${watchlist.id}`, { name: name.trim() }),
    onSuccess: (updated) => {
      replaceList(updated);
      setRenaming(false);
    },
    onError: (error) => push(`Couldn't rename the list: ${error.message}`, "error"),
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/api/watchlists/${watchlist.id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: KEY });
      push(`Deleted "${watchlist.name}".`);
      onDeleted();
    },
    onError: (error) => push(`Couldn't delete the list: ${error.message}`, "error"),
  });
  const first = watchlist.items[0]?.symbol;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      {renaming ? (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) rename.mutate();
          }}
        >
          <input
            autoFocus
            aria-label="List name"
            value={name}
            maxLength={80}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => event.key === "Escape" && setRenaming(false)}
            className="h-8 w-56 rounded-md border border-border bg-background px-2 text-lg font-semibold"
          />
          <Button size="sm" type="submit" variant="primary" disabled={!name.trim()}>
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setRenaming(false)}>
            Cancel
          </Button>
        </form>
      ) : (
        <h1 className="text-lg font-semibold">
          <button
            type="button"
            title="Rename"
            onClick={() => {
              setName(watchlist.name);
              setRenaming(true);
            }}
            className="rounded hover:underline"
          >
            {watchlist.name}
          </button>{" "}
          <span className="text-sm font-normal text-muted">
            {watchlist.items.length} {watchlist.items.length === 1 ? "stock" : "stocks"}
          </span>
        </h1>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {first && (
          <Button
            size="sm"
            variant="primary"
            title="Opens the first stock; [ and ] move through the list"
            onClick={() => {
              useListStore.getState().setList(
                watchlist.name,
                watchlist.items.map((i) => i.symbol),
              );
              router.push(`/stocks/${first}`);
            }}
          >
            Flip through ▸
          </Button>
        )}
        {confirming ? (
          <>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
            <Button size="sm" className="text-fall" onClick={() => remove.mutate()}>
              Delete &quot;{watchlist.name}&quot;
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
            Delete list
          </Button>
        )}
      </div>
    </div>
  );
}

function ListView({ watchlist, onDeleted }: { watchlist: Watchlist; onDeleted: () => void }) {
  const client = useQueryClient();
  const replaceList = useReplaceList();
  const push = useToasts((s) => s.push);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const fail = (what: string) => (error: Error) => {
    push(`Couldn't ${what}: ${error.message}`, "error");
    void client.invalidateQueries({ queryKey: KEY });
  };
  const order = useMutation({
    mutationFn: (symbols: string[]) =>
      api.put<Watchlist>(`/api/watchlists/${watchlist.id}/order`, { symbols }),
    onSuccess: replaceList,
    onError: fail("save the new order"),
  });
  const note = useMutation({
    mutationFn: ({ symbol, text }: { symbol: string; text: string | null }) =>
      api.patch<Watchlist>(`/api/watchlists/${watchlist.id}/items/${symbol}`, { note: text }),
    onSuccess: replaceList,
    onError: fail("save the note"),
  });
  const remove = useMutation({
    mutationFn: (symbol: string) =>
      api.delete<Watchlist>(`/api/watchlists/${watchlist.id}/items/${symbol}`),
    onSuccess: (updated, symbol) => {
      replaceList(updated);
      void client.invalidateQueries({ queryKey: ["membership", symbol] });
    },
    onError: fail("remove the stock"),
  });

  const symbols = watchlist.items.map((i) => i.symbol);
  const list = { source: watchlist.name, symbols };

  function onDragEnd({ active, over }: DragEndEvent) {
    if (!over || active.id === over.id) return;
    const items = arrayMove(
      watchlist.items,
      symbols.indexOf(String(active.id)),
      symbols.indexOf(String(over.id)),
    );
    replaceList({ ...watchlist, items }); // optimistic; the server's answer replaces it
    order.mutate(items.map((i) => i.symbol));
  }

  return (
    <section aria-label={watchlist.name} className="flex min-w-0 flex-1 flex-col gap-3">
      <ListHeader key={watchlist.id} watchlist={watchlist} onDeleted={onDeleted} />
      <AddStock watchlist={watchlist} />
      {watchlist.items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border p-6 text-sm text-muted">
          This list is empty. Add a symbol above, press <kbd>w</kbd> on a stock page, or use ＋ in
          the screener.
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <div
            aria-hidden
            className={cn(GRID, "border-b border-border bg-surface px-2 py-2 text-xs text-muted")}
          >
            <span />
            <span>Symbol</span>
            <span className="text-right">Price</span>
            <span className="text-right">Change</span>
            <span className={cn(WIDE, "text-right")}>RS</span>
            <span className={WIDE}>Setup</span>
            <span className={WIDE}>Stage</span>
            <span className={cn(WIDE, "text-right")}>To pivot</span>
            <span className={WIDE}>Note</span>
            <span />
          </div>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={symbols} strategy={verticalListSortingStrategy}>
              <ul aria-label={`Stocks in ${watchlist.name}`}>
                {watchlist.items.map((item) => (
                  <ItemRow
                    key={item.symbol}
                    item={item}
                    list={list}
                    onNote={(text) => note.mutate({ symbol: item.symbol, text })}
                    onRemove={() => remove.mutate(item.symbol)}
                  />
                ))}
              </ul>
            </SortableContext>
          </DndContext>
        </div>
      )}
      <p className="hidden text-xs text-muted md:block">
        j / k move through the list · Enter opens a stock · [ and ] flip to the previous / next one
        there · drag ⋮⋮ (or Space and the arrow keys) to reorder
      </p>
    </section>
  );
}

function NewList({ onCreated }: { onCreated: (list: Watchlist) => void }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<Watchlist>("/api/watchlists", { name: name.trim() }),
    onSuccess: (list) => {
      client.setQueryData<Watchlist[]>(KEY, (lists) => [...(lists ?? []), list]);
      setOpen(false);
      setName("");
      onCreated(list);
    },
  });
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="w-full rounded-md px-2 py-1.5 text-left text-sm text-muted hover:text-foreground"
      >
        ＋ New watchlist
      </button>
    );
  }
  return (
    <form
      className="flex flex-col gap-1.5 px-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (name.trim()) create.mutate();
      }}
    >
      <input
        autoFocus
        aria-label="New watchlist name"
        placeholder="Name"
        value={name}
        maxLength={80}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => event.key === "Escape" && setOpen(false)}
        className="h-8 rounded-md border border-border bg-background px-2 text-sm"
      />
      {create.error && (
        <p role="alert" className="text-xs text-fall">
          {create.error.message}
        </p>
      )}
      <div className="flex gap-2">
        <Button size="sm" type="submit" variant="primary" disabled={!name.trim()}>
          Create
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/**
 * Watchlists (spec §8.6): several named lists, drag to reorder, a note per stock, and
 * flip-through (open a stock from a list, then `[` / `]`). The open list is `?list=<id>`.
 */
export function Watchlists() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const lists = useQuery({ queryKey: KEY, queryFn: () => api.get<Watchlist[]>("/api/watchlists") });
  const wanted = Number(params.get("list"));
  const all = lists.data ?? [];
  const current = all.find((l) => l.id === wanted) ?? all[0] ?? null;
  const open = (id: number | null) =>
    router.replace(id == null ? pathname : `${pathname}?list=${id}`, { scroll: false });

  return (
    <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-4 px-4 py-4 lg:flex-row">
      <nav aria-label="Watchlists" className="flex shrink-0 flex-col gap-1 lg:w-56">
        <h2 className="px-2 text-xs font-semibold tracking-wide text-muted uppercase">
          Watchlists
        </h2>
        <ul className="flex flex-wrap gap-1 lg:flex-col">
          {all.map((l) => (
            <li key={l.id}>
              <button
                type="button"
                aria-current={l.id === current?.id ? "true" : undefined}
                onClick={() => open(l.id)}
                className={cn(
                  "flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1.5 text-left text-sm",
                  l.id === current?.id
                    ? "bg-surface-2 text-foreground"
                    : "text-muted hover:text-foreground",
                )}
              >
                <span className="truncate">{l.name}</span>
                <span className="tabular text-xs text-muted">{l.items.length}</span>
              </button>
            </li>
          ))}
        </ul>
        <NewList onCreated={(l) => open(l.id)} />
      </nav>
      {lists.isPending && <p className="text-sm text-muted">Loading your watchlists…</p>}
      {lists.error && <p className="text-sm text-fall">{lists.error.message}</p>}
      {lists.data && !current && (
        <p className="flex-1 rounded-lg border border-dashed border-border p-6 text-sm text-muted">
          No watchlists yet. Press <kbd>w</kbd> on any stock page to start one called
          &ldquo;Watchlist&rdquo;, or create a list here.
        </p>
      )}
      {current && (
        <ListView
          key={current.id}
          watchlist={current}
          onDeleted={() => open(all.find((l) => l.id !== current.id)?.id ?? null)}
        />
      )}
    </main>
  );
}
