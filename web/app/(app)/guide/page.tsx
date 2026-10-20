import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import type { Metadata } from "next";
import { Guide } from "@/components/guide/guide";
import { SETTINGS_QUERY } from "@/components/guide/queries";
import { prefetch } from "@/lib/server-api";

export const metadata: Metadata = { title: "Start here · Breakout" };

/** Rendered with the settings so the numbers it quotes are the account's own from the start. */
export default async function GuidePage() {
  const client = new QueryClient();
  await prefetch(client, [SETTINGS_QUERY]);
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-4 px-4 py-6">
        <Guide />
      </main>
    </HydrationBoundary>
  );
}
