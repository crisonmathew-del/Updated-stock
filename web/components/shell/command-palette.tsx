"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Command } from "cmdk";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import { Kbd } from "@/components/ui/kbd";
import { api, type SearchHit } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { isTyping } from "@/lib/keys";
import { applyTheme, currentTheme } from "@/lib/theme";
import { useAddToWatchlist } from "@/lib/use-watchlist";

const DEBOUNCE_MS = 100;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

/** The symbol of the stock page being viewed, if any. */
export function currentSymbol(pathname: string | null): string | null {
  const match = pathname?.match(/^\/stocks\/([^/]+)/);
  return match ? decodeURIComponent(match[1]).toUpperCase() : null;
}

/**
 * ⌘K / Ctrl+K or "/" anywhere: search tickers and companies (server-side, ranked), see price,
 * change, grade and stage, and jump to the stock. Also a few commands.
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const client = useQueryClient();
  const add = useAddToWatchlist();
  const [query, setQuery] = useState("");
  const term = useDebounced(query.trim(), DEBOUNCE_MS);
  const results = useQuery({
    queryKey: ["search", term],
    queryFn: () => api.get<SearchHit[]>(`/api/search?q=${encodeURIComponent(term)}`),
    enabled: open && term.length > 0,
    staleTime: 60_000,
    placeholderData: (previous) => previous,
  });

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenChange(!open);
      } else if (event.key === "/" && !open && !isTyping(event)) {
        event.preventDefault();
        onOpenChange(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  function close() {
    onOpenChange(false);
    setQuery("");
  }

  function go(path: string) {
    close();
    router.push(path);
  }

  function prefetch(symbol: string) {
    router.prefetch(`/stocks/${symbol}`);
    void client.prefetchQuery({
      queryKey: ["stock", symbol, "summary"],
      queryFn: () => api.get(`/api/stocks/${symbol}`),
      staleTime: 60_000,
    });
  }

  const viewing = currentSymbol(pathname);
  const hits = term ? (results.data ?? []) : [];
  const theme = open ? currentTheme() : "dark";

  return (
    <Command.Dialog
      open={open}
      onOpenChange={(next) => (next ? onOpenChange(true) : close())}
      shouldFilter={false}
      label="Search stocks and commands"
      onValueChange={(value) => {
        if (value.startsWith("stock:")) prefetch(value.slice(6));
      }}
      overlayClassName="fixed inset-0 z-40 bg-black/50"
      contentClassName="fixed top-[12vh] left-1/2 z-50 w-[min(640px,92vw)] -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-surface shadow-2xl"
    >
      <div className="flex items-center gap-2 border-b border-border px-3">
        <span aria-hidden className="text-muted">
          ⌕
        </span>
        <Command.Input
          value={query}
          onValueChange={setQuery}
          placeholder="Search a ticker or company…"
          className="h-12 w-full bg-transparent text-base outline-none placeholder:text-muted"
        />
        <Kbd>Esc</Kbd>
      </div>
      <Command.List className="max-h-[60vh] overflow-y-auto p-1.5">
        {term && !results.isFetching && results.isFetched && hits.length === 0 && (
          <p className="px-3 py-4 text-center text-sm text-muted">
            No stock matches “{term}”. Try the ticker or the start of the company name.
          </p>
        )}
        {hits.length > 0 && (
          <Command.Group
            heading="Stocks"
            className="text-xs text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1"
          >
            {hits.map((hit) => (
              <Command.Item
                key={hit.symbol}
                value={`stock:${hit.symbol}`}
                onSelect={() => go(`/stocks/${hit.symbol}`)}
                className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground data-[selected=true]:bg-surface-2"
              >
                <span className="w-16 font-semibold">{hit.symbol}</span>
                <span className="min-w-0 flex-1 truncate text-muted">{hit.name}</span>
                {hit.state && <StageBadge state={hit.state} className="text-xs" />}
                <span className="w-20 text-right">{formatPrice(hit.close)}</span>
                <Change value={hit.change_pct} className="w-20 text-right text-xs" />
                <GradeBadge grade={hit.grade} score={hit.score} />
              </Command.Item>
            ))}
          </Command.Group>
        )}
        <Command.Group
          heading="Go to"
          className="text-xs text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1"
        >
          {[
            ["Dashboard", "/"],
            ["Screener", "/screener"],
            ["Watchlists", "/watchlists"],
          ].map(([label, path]) => (
            <Command.Item
              key={path}
              value={`go:${path}`}
              onSelect={() => go(path)}
              className="cursor-pointer rounded-md px-2 py-2 text-sm text-foreground data-[selected=true]:bg-surface-2"
            >
              Go to {label}
            </Command.Item>
          ))}
          {viewing && (
            <Command.Item
              value={`watch:${viewing}`}
              onSelect={() => {
                add.mutate(viewing);
                close();
              }}
              className="cursor-pointer rounded-md px-2 py-2 text-sm text-foreground data-[selected=true]:bg-surface-2"
            >
              Add {viewing} to watchlist <Kbd>W</Kbd>
            </Command.Item>
          )}
          <Command.Item
            value="theme"
            onSelect={() => {
              applyTheme(theme === "dark" ? "light" : "dark");
              close();
            }}
            className="cursor-pointer rounded-md px-2 py-2 text-sm text-foreground data-[selected=true]:bg-surface-2"
          >
            Switch to {theme === "dark" ? "light" : "dark"} theme
          </Command.Item>
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
