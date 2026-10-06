import type { QueryClient } from "@tanstack/react-query";
import { cookies } from "next/headers";

/**
 * Server-side reads for pages that render with their data (no loading flash, no layout shift):
 * a server component prefetches into a QueryClient and hands it to the client components
 * through `HydrationBoundary`. Requests go straight to the API (API_URL, read at runtime) with
 * the visitor's session cookie. Anything that fails is simply not prefetched: the client
 * fetches it as usual (and a 401 there sends the visitor to sign in).
 */
export type Prefetch = { key: readonly unknown[]; path: string };

const SESSION_COOKIE = "breakout_session";

/** The API's JSON for `path` (which may itself be null), or undefined if the request failed. */
export async function serverGet<T>(path: string): Promise<T | undefined> {
  const session = (await cookies()).get(SESSION_COOKIE);
  if (!session) return undefined;
  try {
    const response = await fetch(`${process.env.API_URL ?? "http://localhost:8000"}${path}`, {
      headers: { accept: "application/json", cookie: `${SESSION_COOKIE}=${session.value}` },
      cache: "no-store",
    });
    return response.ok ? ((await response.json()) as T) : undefined;
  } catch {
    return undefined;
  }
}

export async function prefetch(client: QueryClient, queries: readonly Prefetch[]): Promise<void> {
  await Promise.all(
    queries.map(async ({ key, path }) => {
      const data = await serverGet(path);
      if (data !== undefined) client.setQueryData(key, data);
    }),
  );
}
