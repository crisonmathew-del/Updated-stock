import type { Metadata } from "next";
import { Suspense } from "react";
import { Screener } from "@/components/screener/screener";

export const metadata: Metadata = { title: "Screener · Breakout" };

export default function ScreenerPage() {
  // The screener reads ?preset= / ?screen= on the client, which needs a Suspense boundary.
  return (
    <Suspense>
      <Screener />
    </Suspense>
  );
}
