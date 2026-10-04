// Typed client for the Breakout API. All requests go to this origin's /api/*, which Next.js
// proxies to the FastAPI service (see next.config.ts).

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type ComponentStatus = {
  ok: boolean;
  detail: string | null;
  latency_ms: number | null;
  last_seen: string | null;
};

export type Readiness = {
  status: "ok" | "degraded";
  checked_at: string;
  components: Record<string, ComponentStatus>;
};

async function request(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("Can't reach the API. Check that the api service is running.", null);
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await request(path);
  if (!response.ok) {
    throw new ApiError(`GET ${path} failed with ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

/** Readiness returns 503 with a full body when something is down, so both 200 and 503 parse. */
export async function getReadiness(): Promise<Readiness> {
  const response = await request("/api/health/ready", { cache: "no-store" });
  if (response.status !== 200 && response.status !== 503) {
    throw new ApiError(
      `The API returned ${response.status}. Is the api service healthy?`,
      response.status,
    );
  }
  return (await response.json()) as Readiness;
}
