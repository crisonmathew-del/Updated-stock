import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import type { Metadata } from "next";
import { BuyListView } from "@/components/buy-list/buy-list";
import { BUY_LIST } from "@/components/buy-list/queries";
import { prefetch } from "@/lib/server-api";

export const metadata: Metadata = { title: "Buy list · Breakout" };

/** Rendered with its data so the orders are there on first paint. */
export default async function BuyListPage() {
  const client = new QueryClient();
  await prefetch(client, [BUY_LIST]);
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-4 px-4 py-6">
        <BuyListView />
      </main>
    </HydrationBoundary>
  );
}
