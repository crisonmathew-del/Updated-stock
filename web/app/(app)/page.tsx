import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import type { Metadata } from "next";
import { Dashboard } from "@/components/dashboard/dashboard";
import { DASHBOARD } from "@/components/dashboard/queries";
import { prefetch } from "@/lib/server-api";

export const metadata: Metadata = { title: "Dashboard · Breakout" };

/** Rendered with its data (fetched here, in parallel) so panels never jump as they load. */
export default async function DashboardPage() {
  const client = new QueryClient();
  await prefetch(client, Object.values(DASHBOARD));
  return (
    <HydrationBoundary state={dehydrate(client)}>
      <Dashboard />
    </HydrationBoundary>
  );
}
