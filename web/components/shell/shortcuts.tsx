"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { currentSymbol } from "@/components/shell/command-palette";
import { isTyping, plainKey } from "@/lib/keys";
import { useAddToWatchlist } from "@/lib/use-watchlist";
import { neighbours, useListStore } from "@/stores/list";

/**
 * Moves focus to the next (`step` 1) or previous (−1) list row marked `data-nav-row`, starting
 * from the focused one (or the first row when none is focused). Returns the row it focused.
 */
export function focusRow(step: 1 | -1, root: ParentNode = document): HTMLElement | null {
  const rows = Array.from(root.querySelectorAll<HTMLElement>("[data-nav-row]"));
  if (rows.length === 0) return null;
  const current = rows.findIndex((row) => row === document.activeElement);
  const next = current < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, current + step));
  const row = rows[next];
  row.focus();
  row.scrollIntoView({ block: "nearest" });
  return row;
}

/**
 * Global shortcuts (spec §8.1): `j` / `k` move through list rows (Enter opens one), `w` adds
 * the stock being viewed to the watchlist, `[` / `]` flip to the previous / next stock of the
 * list it was opened from. (`/` and ⌘K open search; `1`-`5` change the chart timeframe.) A
 * view with its own keyboard handling (the virtualised screener) calls `preventDefault()`
 * first, in the capture phase, and these handlers stand down.
 */
export function Shortcuts() {
  const router = useRouter();
  const pathname = usePathname();
  const add = useAddToWatchlist();
  const symbols = useListStore((s) => s.symbols);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.defaultPrevented || isTyping(event) || !plainKey(event)) return;
      if (event.key === "j" || event.key === "k") {
        if (focusRow(event.key === "j" ? 1 : -1)) event.preventDefault();
        return;
      }
      const symbol = currentSymbol(pathname);
      if (!symbol) return;
      if (event.key === "w") {
        event.preventDefault();
        add.mutate(symbol);
      } else if (event.key === "[" || event.key === "]") {
        const [previous, next] = neighbours(symbols, symbol);
        const target = event.key === "[" ? previous : next;
        if (target) {
          event.preventDefault();
          router.push(`/stocks/${target}`);
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pathname, symbols, router, add]);

  return null;
}
