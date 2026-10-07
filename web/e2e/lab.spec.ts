import { expect, type Page, test } from "@playwright/test";
import { E2E_EMAIL, E2E_PASSWORD } from "../playwright.config";

/**
 * Phase 7 journeys on the seeded market: the backtest lab (the seed ran a short backtest over
 * the sessions around SPOT's breakout: one trade), the signal performance page, the settings
 * page, and the AI summary's switched-off state (no ANTHROPIC_API_KEY in e2e).
 */

async function signIn(page: Page, next = "/") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(E2E_EMAIL);
  await page.getByLabel("Password").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => url.pathname === next);
}

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

test("read a backtest report and a trade's chart, then start another run", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/backtests");

  const runs = page.getByRole("table", { name: "Backtest runs, newest first" });
  const seeded = "Setups graded any grade, 2025-03-14 to 2025-05-02";
  await runs.getByRole("link", { name: seeded }).click();
  await page.waitForURL(/\/backtests\/\d+$/);

  // Labelled hypothetical, with the survivorship caveat, headline numbers and the equity chart.
  await expect(page.getByRole("heading", { level: 1, name: seeded })).toBeVisible();
  await expect(page.getByRole("note").first()).toContainText("Hypothetical.");
  await expect(page.getByText(/Survivorship bias/).first()).toBeVisible();
  const headline = page.getByRole("group", { name: "Headline numbers" });
  await expect(headline.getByText("CAGR")).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();

  // The one trade: SPOT, bought the session after its breakout; its chart opens on request.
  const trades = page.getByRole("table", { name: "Every simulated trade" });
  await trades.getByRole("button", { name: "SPOT" }).click();
  await expect(page.getByText("Trade 1: SPOT")).toBeVisible();
  await expect(
    page.getByRole("img", { name: /SPOT daily chart with the trade's entry/ }),
  ).toBeVisible();

  // A new run with the default rules is queued for the worker (none runs in e2e).
  await page.getByRole("link", { name: "Backtests", exact: true }).click();
  await page.waitForURL("**/backtests");
  const form = page.getByRole("form", { name: "New backtest" });
  await form.getByLabel("Name (optional)").fill("e2e default rules");
  await form.getByRole("button", { name: "Run backtest" }).click();
  await page.waitForURL(/\/backtests\/\d+$/);
  await expect(page.getByRole("heading", { level: 1, name: "e2e default rules" })).toBeVisible();
  await expect(page.getByText("Waiting for the worker…")).toBeVisible();
  expect(errors).toEqual([]);
});

test("signal performance lists the logged signals by type and grade", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/performance");

  const table = page.getByRole("table", { name: /20 sessions after each signal/ });
  await expect(table.getByRole("rowheader").first()).toBeVisible();
  await page
    .getByRole("radiogroup", { name: "Measured after" })
    .getByRole("radio", { name: "5 sessions" })
    .click();
  await expect(page.getByRole("table", { name: /5 sessions after each signal/ })).toBeVisible();
  expect(errors).toEqual([]);
});

test("change a setting, see it kept, put it back; keys say what's configured", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/settings");

  const keys = page.getByRole("table", { name: /whether its key is set/ });
  await expect(
    keys.getByRole("row").filter({ has: page.getByRole("rowheader", { name: /Anthropic/ }) }),
  ).toContainText("Not set");

  const size = page.getByRole("spinbutton", { name: "Account size" });
  await expect(size).toHaveValue("100000");
  await size.fill("50000");
  await page.getByRole("button", { name: "Save 1 change" }).click();
  await expect(page.getByRole("button", { name: "No changes" })).toBeDisabled();

  await page.reload();
  await expect(size).toHaveValue("50000");
  await page.getByRole("button", { name: "Reset “Account size” to its default" }).click();
  await expect(size).toHaveValue("100000");
  await page.reload();
  await expect(size).toHaveValue("100000");
  expect(errors).toEqual([]);
});

test("the AI summary says how to switch it on", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/stocks/SPOT");
  await expect(page.getByText(/ANTHROPIC_API_KEY/)).toBeVisible();
  expect(errors).toEqual([]);
});
