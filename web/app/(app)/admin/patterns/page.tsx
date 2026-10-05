import type { Metadata } from "next";
import { ReviewBoard } from "@/components/patterns/review-board";

export const metadata: Metadata = { title: "Patterns · Breakout" };

export default function PatternsPage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Pattern review</h1>
        <p className="text-muted">
          Check what the detectors find before they feed the scores in Phase 4. Each chart shows the
          base as detected: swing points, contraction depths, the pivot and the base low.
        </p>
      </header>
      <ReviewBoard />
    </main>
  );
}
