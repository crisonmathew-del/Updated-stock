import type { Metadata } from "next";
import { PerformanceView } from "@/components/performance/performance";

export const metadata: Metadata = { title: "Signal performance · Breakout" };

export default function PerformancePage() {
  return <PerformanceView />;
}
