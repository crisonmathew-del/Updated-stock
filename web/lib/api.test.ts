import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test-utils";
import { api, ApiError } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("sends the CSRF header and JSON on writes but not on reads", async () => {
    const fetchMock = mockApi({
      "POST /api/admin/backfill": { status: 202, body: { job: "backfill", job_id: "x" } },
      "GET /api/admin/jobs": { body: [] },
    });

    await api.post("/api/admin/backfill", { force: true });
    await api.get("/api/admin/jobs");

    const [, postInit] = fetchMock.mock.calls[0];
    expect(postInit?.headers).toMatchObject({
      "X-Requested-With": "breakout",
      "Content-Type": "application/json",
    });
    expect(postInit?.body).toBe('{"force":true}');
    const [, getInit] = fetchMock.mock.calls[1];
    expect(getInit?.headers).not.toHaveProperty("X-Requested-With");
  });

  it("turns API error details into readable messages", async () => {
    mockApi({
      "PATCH /api/settings": {
        status: 422,
        body: {
          detail: [
            { key: "max_position_pct", message: "Input should be less than or equal to 100" },
          ],
        },
      },
      "POST /api/admin/eod-update": {
        status: 409,
        body: { detail: "Another data job is running." },
      },
    });

    await expect(api.patch("/api/settings", { changes: {} })).rejects.toThrow(
      "max_position_pct: Input should be less than or equal to 100",
    );
    const conflict = await api.post("/api/admin/eod-update").catch((e: ApiError) => e);
    expect(conflict).toBeInstanceOf(ApiError);
    expect((conflict as ApiError).status).toBe(409);
  });

  it("sends the browser to sign-in when the session has expired", async () => {
    mockApi({
      "GET /api/admin/universe": { status: 401, body: { detail: "Sign in to continue." } },
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { pathname: "/admin/data", search: "?x=1", assign });

    await expect(api.get("/api/admin/universe")).rejects.toThrow("Sign in to continue.");
    expect(assign).toHaveBeenCalledWith("/login?next=%2Fadmin%2Fdata%3Fx%3D1");
  });
});
