import type { Metadata } from "next";
import { GroupsPanel } from "@/components/inspect/groups-panel";
import { RegimePanel } from "@/components/inspect/regime-panel";
import { StockPanel } from "@/components/inspect/stock-panel";

export const metadata: Metadata = { title: "Inspect · Breakout" };

export default function InspectPage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Inspect analytics</h1>
        <p className="text-muted">
          Check the computed values against your charting platform. The full stock page with charts
          comes in Phase 5.
        </p>
      </header>
      <StockPanel />
      <RegimePanel />
      <GroupsPanel />
    </main>
  );
}
