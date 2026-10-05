"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Watchlist } from "@/lib/api";
import { useToasts } from "@/stores/toast";

/** Add a stock to the first watchlist (created as "Watchlist" if there is none). */
export function useAddToWatchlist() {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  return useMutation({
    mutationFn: (symbol: string) =>
      api.post<Watchlist>("/api/watchlists/default/items", { symbol }),
    onSuccess: (list, symbol) => {
      push(`Added ${symbol.toUpperCase()} to ${list.name}.`);
      void client.invalidateQueries({ queryKey: ["watchlists"] });
      void client.invalidateQueries({ queryKey: ["membership", symbol.toUpperCase()] });
    },
    onError: (error, symbol) =>
      push(`Couldn't add ${symbol.toUpperCase()}: ${error.message}`, "error"),
  });
}
