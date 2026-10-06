import { expect, type Page, test } from "@playwright/test";
import { E2E_EMAIL, E2E_PASSWORD } from "../playwright.config";

/**
 * Phase 6 journeys on the seeded market (SPOT broke out above its 92.46 pivot and closed at
 * 94.00; plan entry 92.56, stop 88.71): an alert arrives over the live socket (through the
 * Next.js proxy) as a toast and on the bell; alert rules and the alerts history; a position
 * added from the trade plan with its P&L in R; the live board and the intraday chart.
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

test("an alert arrives live; rules and the alerts history", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/alerts");
  await expect(page.getByText("Live: new alerts appear as they happen")).toBeVisible();

  // A test alert goes API → Redis → the socket → a toast and the bell, without a reload.
  await page.getByRole("tab", { name: "Channels" }).click();
  await page.getByRole("button", { name: "Send a test in-app alert" }).click();
  await expect(page.getByText("▲ Test alert (in-app)")).toBeVisible();
  await expect(page.getByRole("button", { name: "Alerts, 1 unread" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "In-app sent" })).toBeVisible();

  // A rule from the builder, described in plain English.
  await page.getByRole("tab", { name: "Rules" }).click();
  const form = page.getByRole("form", { name: "New alert rule" });
  await form.getByRole("textbox", { name: "Stock" }).fill("SPOT");
  await form.getByLabel("Price", { exact: true }).fill("100");
  await form.getByRole("button", { name: "Create rule" }).click();
  await expect(
    page.getByText("When SPOT trades above 100.00: email and in-app, high priority."),
  ).toBeVisible();

  // The history shows the test alert with what each channel did; the bell clears.
  await page.getByRole("tab", { name: "History" }).click();
  const history = page.getByRole("list", { name: "Alerts" });
  await expect(history.getByText("Test alert (in-app)")).toBeVisible();
  await expect(history.getByText("In-app sent")).toBeVisible();
  await page.getByRole("button", { name: "Alerts, 1 unread" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Mark all read" }).click();
  await expect(page.getByRole("button", { name: "Alerts", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("a position from the trade plan, the live board and the intraday chart", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, "/stocks/SPOT");
  await page.getByRole("link", { name: "I bought this" }).click();
  await page.waitForURL("**/holdings?**");
  const add = page.getByRole("form", { name: "Add a position" });
  await expect(add.getByRole("textbox", { name: "Stock" })).toHaveValue("SPOT");
  await expect(add.getByLabel("Entry")).toHaveValue("92.56");
  await add.getByRole("button", { name: "Add position" }).click();
  // At the 94.00 close: (94.00 − 92.56) ÷ (92.56 − 88.71) = +0.37R.
  const positions = page.getByRole("table", { name: "Your positions" });
  await expect(positions.getByRole("row").filter({ hasText: "SPOT" })).toContainText("+0.37R");

  await page.getByRole("link", { name: "Live", exact: true }).click();
  await page.waitForURL("**/live");
  const board = page.getByRole("table", { name: /Setups near or past their pivot/ });
  await expect(board.getByRole("link", { name: "SPOT" })).toBeVisible();

  await board.getByRole("link", { name: "SPOT" }).click();
  await page.waitForURL("**/stocks/SPOT");
  await page.getByRole("button", { name: "1D 1m" }).click();
  await expect(page.getByText(/No minute bars recorded for this stock yet/)).toBeVisible();
  expect(errors).toEqual([]);
});
