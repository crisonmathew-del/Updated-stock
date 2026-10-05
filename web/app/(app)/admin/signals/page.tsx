import type { Metadata } from "next";
import { SignalLog } from "@/components/setups/signal-log";

export const metadata: Metadata = { title: "Signals · Breakout" };

export default function SignalsPage() {
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Signals</h1>
        <p className="text-muted">
          What the scanner said and when, and what happened next. Outcomes update after each close
          until 60 sessions have passed.
        </p>
      </header>
      <SignalLog />
    </main>
  );
}
