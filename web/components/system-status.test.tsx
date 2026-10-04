import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Readiness } from "@/lib/api";
import { SystemStatus } from "./system-status";

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SystemStatus />
    </QueryClientProvider>,
  );
}

function readiness(overrides: Partial<Readiness["components"]> = {}): Readiness {
  const up = { ok: true, detail: null, latency_ms: null, last_seen: null };
  const components = {
    api: up,
    postgres: { ...up, detail: "PostgreSQL 16.10", latency_ms: 1.2 },
    timescaledb: { ...up, detail: "TimescaleDB 2.22.0" },
    redis: up,
    worker: up,
    scheduler: up,
    streamer: up,
    ...overrides,
  } as Readiness["components"];
  const ok = Object.values(components).every((c) => c.ok);
  return { status: ok ? "ok" : "degraded", checked_at: "2026-10-05T14:30:00Z", components };
}

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SystemStatus", () => {
  it("shows every service as up when the API is ready", async () => {
    mockFetch(200, readiness());
    renderWithClient();

    expect(await screen.findByText("All services up")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL 16.10 · 1.2 ms")).toBeInTheDocument();
    expect(screen.getByText("TimescaleDB 2.22.0")).toBeInTheDocument();
    expect(screen.queryAllByText("down:")).toHaveLength(0);
  });

  it("names the service that is down when readiness returns 503", async () => {
    mockFetch(
      503,
      readiness({
        streamer: { ok: false, detail: "no recent heartbeat", latency_ms: null, last_seen: null },
      }),
    );
    renderWithClient();

    expect(await screen.findByText("Some services are down")).toBeInTheDocument();
    const streamerRow = screen.getByText("Streamer").closest("li");
    expect(streamerRow).toHaveTextContent("down: no recent heartbeat");
  });

  it("explains how to fix it when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderWithClient();

    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
    expect(screen.getByText(/Can't reach the API/)).toBeInTheDocument();
    expect(screen.getByText("make dev")).toBeInTheDocument();
  });
});
