"use client";

import Link from "next/link";
import type { ComponentProps } from "react";
import { useListStore } from "@/stores/list";

type Props = Omit<ComponentProps<typeof Link>, "href"> & {
  symbol: string;
  /** The list this link belongs to, so `[` / `]` on the stock page flip through it. */
  list: { source: string; symbols: string[] };
};

/**
 * A link to a stock page from a list row. Opening it makes that list the one `[` / `]` move
 * through, and `j` / `k` move focus between rows marked like this one.
 */
export function StockLink({ symbol, list, onClick, ...rest }: Props) {
  return (
    <Link
      {...rest}
      href={`/stocks/${symbol}`}
      data-nav-row=""
      onClick={(event) => {
        useListStore.getState().setList(list.source, list.symbols);
        onClick?.(event);
      }}
    />
  );
}
