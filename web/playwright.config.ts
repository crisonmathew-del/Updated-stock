import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests (spec §12): a production build of the web app against a real API on its own
 * database. Before the API starts, `tests.e2e_seed` recreates `breakout_e2e` with the drawn VCP
 * breakout market and the login user (it refuses any database not named `*_e2e`).
 *
 * Needs Postgres and Redis on localhost (`make dev` publishes them), uv and pnpm, and a
 * Chromium for Playwright (`pnpm exec playwright install chromium`). Run with `make e2e`.
 */
const API_PORT = 8100;
const WEB_PORT = 3100;

export const E2E_EMAIL = process.env.E2E_EMAIL ?? "owner@example.com";
export const E2E_PASSWORD = process.env.E2E_PASSWORD ?? "e2e password 1234";

export default defineConfig({
  testDir: "e2e",
  // One seeded database shared by every test: run them one at a time, in file order.
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: [
    {
      command: `uv run python -m tests.e2e_seed && uv run uvicorn app.main:app --port ${API_PORT}`,
      cwd: "../api",
      url: `http://localhost:${API_PORT}/api/health`,
      env: {
        DATABASE_URL:
          process.env.E2E_DATABASE_URL ??
          "postgresql+asyncpg://breakout:breakout@localhost:5432/breakout_e2e",
        REDIS_URL: process.env.E2E_REDIS_URL ?? "redis://localhost:6379/13",
        E2E_EMAIL,
        E2E_PASSWORD,
      },
      timeout: 180_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
    {
      // `output: "standalone"`: serve the build the way the Docker image does.
      command:
        "pnpm build && cp -r .next/static .next/standalone/.next/ && " +
        "{ [ ! -d public ] || cp -r public .next/standalone/; } && " +
        "node .next/standalone/server.js",
      url: `http://localhost:${WEB_PORT}/login`,
      env: {
        API_URL: `http://localhost:${API_PORT}`,
        PORT: String(WEB_PORT),
        HOSTNAME: "localhost",
        NEXT_TELEMETRY_DISABLED: "1",
      },
      timeout: 300_000,
      reuseExistingServer: false,
    },
  ],
});
