"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { currentSymbol } from "@/components/shell/command-palette";
import { isTyping, plainKey } from "@/lib/keys";
import { useAddToWatchlist } from "@/lib/use-watchlist";
import { neighbours, useListStore } from "@/stores/list";

/**
 * Global shortcuts (spec §8.1): `w` adds the stock being viewed to the watchlist, `[` / `]`
 * flip to the previous / next stock of the list it was opened from. (`/` and ⌘K open search;
 * `j`/`k` move through lists and `1`-`5` change the chart timeframe where those exist.)
 */
export function Shortcuts() {
  const router = useRouter();
  const pathname = usePathname();
  const add = useAddToWatchlist();
  const symbols = useListStore((s) => s.symbols);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTyping(event) || !plainKey(event)) return;
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
