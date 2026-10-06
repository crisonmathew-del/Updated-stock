import type { Metadata } from "next";
import { Suspense } from "react";
import { AlertsCentre } from "@/components/alerts/alerts-centre";

export const metadata: Metadata = { title: "Alerts · Breakout" };

export default function AlertsPage() {
  // The tab comes from ?tab= on the client, which needs a Suspense boundary.
  return (
    <Suspense>
      <AlertsCentre />
    </Suspense>
  );
}
