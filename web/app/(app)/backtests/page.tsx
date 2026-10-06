import type { Metadata } from "next";
import { NewRun } from "@/components/backtests/new-run";
import { RunList } from "@/components/backtests/run-list";
import { Section } from "@/components/ui/section";

export const metadata: Metadata = { title: "Backtest lab · Breakout" };

export default function BacktestsPage() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Backtest lab</h1>
        <p className="text-sm text-muted">
          Replays the scan&apos;s own rules over history, using only what was known each day, then
          trades the setups with your portfolio rules. Results are hypothetical.
        </p>
      </div>
      <Section title="Runs">
        <RunList />
      </Section>
      <Section title="New run">
        <NewRun />
      </Section>
    </div>
  );
}
