import { create } from "zustand";

/**
 * The list the user is flipping through (a screen, a watchlist, a dashboard panel), so `[` and
 * `]` on a stock page move to the previous / next stock in it.
 */
type ListState = {
  source: string | null;
  symbols: string[];
  setList: (source: string, symbols: string[]) => void;
};

export const useListStore = create<ListState>((set) => ({
  source: null,
  symbols: [],
  setList: (source, symbols) => set({ source, symbols }),
}));

/** The neighbours of `symbol` in the current list (null at either end or when absent). */
export function neighbours(symbols: string[], symbol: string): [string | null, string | null] {
  const i = symbols.indexOf(symbol);
  if (i < 0) return [null, null];
  return [symbols[i - 1] ?? null, symbols[i + 1] ?? null];
}
