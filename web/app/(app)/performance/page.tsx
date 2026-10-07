import type { Metadata } from "next";
import { PerformanceView } from "@/components/performance/performance";

export const metadata: Metadata = { title: "Signal performance · Breakout" };

export default function PerformancePage() {
  return (
    <main className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-4 py-6">
      <PerformanceView />
    </main>
  );
}
