import Link from "next/link";
import { SystemStatus } from "@/components/system-status";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-12">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">System status</h1>
        <p className="text-muted">
          Every service, checked through the API&apos;s readiness endpoint. Market data, backfill
          progress and data health are on the{" "}
          <Link href="/admin/data" className="text-foreground underline underline-offset-2">
            Data page
          </Link>
          .
        </p>
      </header>
      <SystemStatus />
    </main>
  );
}
