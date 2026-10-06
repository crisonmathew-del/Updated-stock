import type { Metadata } from "next";
import { Suspense } from "react";
import { Holdings } from "@/components/holdings/holdings";

export const metadata: Metadata = { title: "Holdings · Breakout" };

export default function HoldingsPage() {
  // The add form reads ?symbol=&entry=&stop= (from a stock's trade plan) on the client.
  return (
    <Suspense>
      <Holdings />
    </Suspense>
  );
}
