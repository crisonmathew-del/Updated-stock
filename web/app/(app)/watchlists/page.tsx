import type { Metadata } from "next";
import { Suspense } from "react";
import { Watchlists } from "@/components/watchlists/watchlists";

export const metadata: Metadata = { title: "Watchlists · Breakout" };

export default function WatchlistsPage() {
  // The open list comes from ?list= on the client, which needs a Suspense boundary.
  return (
    <Suspense>
      <Watchlists />
    </Suspense>
  );
}
