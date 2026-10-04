import { SystemStatus } from "@/components/system-status";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Breakout</h1>
        <p className="text-muted">
          Growth-stock scanning, scoring and alerts. The scaffold is running. This page checks every
          service through the API&apos;s readiness endpoint.
        </p>
      </header>
      <SystemStatus />
      <footer className="mt-auto text-xs text-muted">
        Screening signals, not financial advice.
      </footer>
    </main>
  );
}
