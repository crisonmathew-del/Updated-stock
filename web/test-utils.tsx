import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Stub fetch with a handler keyed by "METHOD /path". */
export function mockApi(routes: Record<string, { status?: number; body?: unknown }>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const url = typeof input === "string" ? input : input.toString();
    const route = routes[`${method} ${url}`];
    if (!route) return new Response(JSON.stringify({ detail: "not mocked" }), { status: 500 });
    return new Response(route.status === 204 ? null : JSON.stringify(route.body ?? {}), {
      status: route.status ?? 200,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
