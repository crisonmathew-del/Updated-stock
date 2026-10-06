import { expect, type Page, test } from "@playwright/test";
import { E2E_EMAIL, E2E_PASSWORD } from "../playwright.config";

/**
 * The core journey (spec §12): search → stock page → add to watchlist, plus the screener and
 * the dashboard, on the seeded market: SPOT drew a four-contraction VCP and broke out above its
 * 92.46 pivot (entry 92.56, stop 88.71); AAPL trends steadily. (The alert-rule step arrives
 * with alerts in Phase 6.)
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

test("search a ticker, see its full analysis with the pattern drawn, add it to a watchlist", async ({
  page,
}) => {
  const errors = watchErrors(page);
  await signIn(page);

  // ⌘K / Ctrl+K search by company name; Enter opens the best match.
  await page.keyboard.press("ControlOrMeta+k");
  const search = page.getByPlaceholder("Search a ticker or company…");
  await search.fill("spotify");
  await expect(page.getByRole("option", { name: /SPOT/ }).first()).toBeVisible();
  await search.press("Enter");
  await page.waitForURL("**/stocks/SPOT");

  // Header, chart with the base drawn (its label and pivot), score and trade plan.
  await expect(page.getByRole("heading", { level: 1, name: "SPOT" })).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
  await expect(page.getByText(/pivot 92\.46/).first()).toBeVisible();
  const plan = page.getByRole("region", { name: "Trade plan" });
  await expect(plan.getByLabel(/Entry/)).toHaveValue("92.56");
  await expect(plan.getByLabel(/Stop/)).toHaveValue("88.71");
  await expect(page.getByRole("region", { name: "Trend Template" })).toBeVisible();

  // `w` adds the stock to the watchlist (created on first use).
  await page.keyboard.press("w");
  await expect(page.getByText("Added SPOT to Watchlist.")).toBeVisible();

  await page.getByRole("link", { name: "Watchlists" }).click();
  const list = page.getByRole("list", { name: "Stocks in Watchlist" });
  await expect(list.getByRole("link", { name: /SPOT/ })).toBeVisible();
  expect(errors).toEqual([]);
});

test("screen the market, preview a result and flip through the results", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/screener");
  await page.goto("/screener?preset=all");

  const grid = page.getByRole("grid", { name: "Screener results" });
  await expect(grid.getByRole("row", { name: /SPOT/ })).toBeVisible();
  await expect(grid.getByRole("row", { name: /AAPL/ })).toBeVisible();

  await grid.getByRole("row", { name: /SPOT/ }).click();
  const preview = page.getByRole("complementary", { name: "SPOT preview" });
  await expect(preview.locator("canvas").first()).toBeVisible();

  // Enter opens the selected stock; `]` flips to the next one in the results.
  const order = await grid
    .getByRole("row")
    .evaluateAll((rows) => rows.slice(1).map((r) => r.id.replace("screener-row-", "")));
  await page.keyboard.press("Enter");
  await page.waitForURL("**/stocks/SPOT");
  const next = order[order.indexOf("SPOT") + 1];
  if (next) {
    await page.keyboard.press("]");
    await page.waitForURL(`**/stocks/${next}`);
  }
  expect(errors).toEqual([]);
});

test("the dashboard leads with the market and lists the breakout", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await expect(page.getByRole("region", { name: "Market" })).toContainText("Confirmed uptrend");
  const breakouts = page.getByRole("list", { name: "Recent breakouts" });
  await expect(breakouts.getByRole("link", { name: /SPOT/ })).toContainText("Breakout");
  expect(errors).toEqual([]);
});
