import type { Metadata } from "next";
import { BackfillPanel } from "@/components/admin/backfill-panel";
import { DataHealthPanel } from "@/components/admin/data-health-panel";
import { JobsPanel } from "@/components/admin/jobs-panel";
import { UniversePanel } from "@/components/admin/universe-panel";

export const metadata: Metadata = { title: "Data · Breakout" };

export default function DataAdminPage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Market data</h1>
        <p className="text-muted">
          The universe, its price history and data health. Prices are updated automatically 20
          minutes after each close.
        </p>
      </header>
      <DataHealthPanel />
      <BackfillPanel />
      <UniversePanel />
      <JobsPanel />
    </main>
  );
}
