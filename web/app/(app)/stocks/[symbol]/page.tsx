import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import type { Metadata } from "next";
import { SETTINGS_QUERY, stockQueries } from "@/components/stock/queries";
import { StockPage } from "@/components/stock/stock-page";
import { prefetch } from "@/lib/server-api";

export async function generateMetadata({
  params,
}: PageProps<"/stocks/[symbol]">): Promise<Metadata> {
  const { symbol } = await params;
  return { title: `${decodeURIComponent(symbol).toUpperCase()} · Breakout` };
}

/**
 * Rendered with what sits above the fold (header, score, trade plan and the settings it sizes
 * with), fetched here in parallel, so nothing there jumps as it loads. The rest loads on the
 * client: the chart's box has a fixed height (inlining ~60 KiB of bars in the HTML slowed the
 * first paint on slow connections), and the panels below the fold can't shift what's in view.
 */
export default async function Page({ params }: PageProps<"/stocks/[symbol]">) {
  const symbol = decodeURIComponent((await params).symbol).toUpperCase();
  const client = new QueryClient();
  const q = stockQueries(symbol);
  await prefetch(client, [q.summary, q.setup, SETTINGS_QUERY]);
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <StockPage symbol={symbol} />
    </HydrationBoundary>
  );
}
