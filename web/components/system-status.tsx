"use client";

import { useQuery } from "@tanstack/react-query";
import { getReadiness, type ComponentStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const REFRESH_MS = 5_000;

// Display order and labels for each component reported by GET /api/health/ready.
const COMPONENTS: { key: string; label: string }[] = [
  { key: "api", label: "API" },
  { key: "postgres", label: "PostgreSQL" },
  { key: "timescaledb", label: "TimescaleDB" },
  { key: "redis", label: "Redis" },
  { key: "worker", label: "Worker" },
  { key: "scheduler", label: "Scheduler" },
  { key: "streamer", label: "Streamer" },
];

function StatusRow({ label, status }: { label: string; status: ComponentStatus | undefined }) {
  const ok = status?.ok ?? false;
  return (
    <li className="flex items-center justify-between gap-4 py-2.5">
      <span className="flex items-center gap-3">
        <span
          aria-hidden
          className={cn("w-4 text-center font-semibold", ok ? "text-ok" : "text-fail")}
        >
          {ok ? "✓" : "✕"}
        </span>
        <span>{label}</span>
      </span>
      <span className="tabular text-right text-sm text-muted">
        <span className="sr-only">{ok ? "up" : "down"}: </span>
        {status?.detail ?? (ok ? "up" : "down")}
        {status?.latency_ms != null && ` · ${status.latency_ms} ms`}
      </span>
    </li>
  );
}

export function SystemStatus() {
  const { data, error, isPending } = useQuery({
    queryKey: ["health", "ready"],
    queryFn: getReadiness,
    refetchInterval: REFRESH_MS,
    retry: false,
  });

  return (
    <section aria-labelledby="system-status-heading" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h2 id="system-status-heading" className="text-sm font-medium">
          System status
        </h2>
        <span role="status" className="text-sm text-muted">
          {isPending && "Checking…"}
          {error && "API unreachable"}
          {data && (data.status === "ok" ? "All services up" : "Some services are down")}
        </span>
      </div>

      {error ? (
        <p className="rounded-md border border-border p-4 text-sm">
          {error.message} Start the stack with <code>make dev</code>, then this page refreshes
          automatically.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border px-4">
          <StatusRow
            label="Web"
            status={{ ok: true, detail: "Next.js", latency_ms: null, last_seen: null }}
          />
          {COMPONENTS.map(({ key, label }) => (
            <StatusRow key={key} label={label} status={data?.components[key]} />
          ))}
        </ul>
      )}
    </section>
  );
}
