import type { Metadata } from "next";
import { SetupsBoard } from "@/components/setups/setups-board";

export const metadata: Metadata = { title: "Setups · Breakout" };

export default function SetupsPage() {
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Setups</h1>
        <p className="text-muted">
          Every stock with a base, an earnings gap or trend leadership, scored after each close,
          with its stage, trade plan and the reasons behind every change. The real setups board
          arrives with the Phase 5 design.
        </p>
      </header>
      <SetupsBoard />
    </main>
  );
}
