import { expect, type Page, test } from "@playwright/test";
import { E2E_EMAIL, E2E_PASSWORD } from "../playwright.config";

/**
 * The Phase 8 journey: what the app says to do. On the seeded market SPOT broke out of its VCP
 * but has no SEC statements, so the rules say don't buy (6 of 7 pass) and the buy list has no
 * orders; it was on the breakout watch the session before, so the watch's record is 1 hit.
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

test("the buy list, the stock's verdict in plain English, and the guide", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/buy-list");

  await expect(page.getByRole("heading", { level: 1, name: "Buy list" })).toBeVisible();
  await expect(page.getByRole("region", { name: "The market today" })).toContainText(
    "Market: Confirmed uptrend",
  );
  const orders = page.getByRole("region", { name: "Orders for the next session" });
  await expect(orders).toContainText("No orders tonight: no stock passes all 7 rules.");
  await expect(page.getByRole("region", { name: "Breakout watch" })).toContainText(
    "1 pick. ✓ 1 broke out within 10 sessions",
  );

  // The "?" explains a term in plain English.
  await orders.getByRole("button", { name: "What is Buy-stop order?" }).click();
  await expect(page.getByText(/An order you give your broker in advance/)).toBeVisible();
  await page.keyboard.press("Escape");

  // The stock page says what to do, every rule with its numbers, and the stock in plain words.
  await page.goto("/stocks/SPOT");
  const verdict = page.getByRole("region", { name: "What to do" });
  await expect(verdict).toContainText("Don't buy");
  await expect(verdict).toContainText("6 of 7 rules pass");
  await expect(verdict).toContainText("It fails 1 of the app's 7 rules: its finances are unknown.");
  await expect(verdict.getByRole("list", { name: "The app's buy rules" })).toContainText(
    "Stop 88.71, 5.6% below 94.00.",
  );
  await expect(verdict).toContainText("It has done better than 99% of all stocks");

  // Start here: the routine and every term.
  await page.getByRole("button", { name: "Admin ▾" }).click();
  await page.getByRole("menuitem", { name: "Start here (guide)" }).click();
  await page.waitForURL("**/guide");
  await expect(page.getByRole("heading", { level: 1, name: "Start here" })).toBeVisible();
  await expect(page.getByRole("region", { name: "The 7 rules for a buy" })).toContainText(
    "An RS Rating of 80 or more",
  );
  await page.getByRole("link", { name: "buy-stop order" }).click();
  await expect(page.locator("#term-buy_stop")).toBeInViewport();
  expect(errors).toEqual([]);
});
