import { expect, test } from "@playwright/test";
import { LOGINS, PLATFORM, signIn } from "../helpers";

// Robot 15: when a refresh fails, the patient and platform apps keep what they last loaded and say
// plainly that it is old and since when (GHE-15 section 05), instead of showing it as current.
const OLD = /The information below is old: it was last updated at/;

test("the patient app says its information is old when a refresh fails", async ({ page }) => {
  await page.clock.install();
  await signIn(page, "patient", LOGINS.patient);
  await expect(page.getByText(/Welcome back, /)).toBeVisible();      // the first load has finished
  await expect(page.getByText(OLD)).toHaveCount(0);

  await page.route("**/api/v1/me", (route) => route.abort());     // the backend stops answering
  await page.clock.runFor(61_000);                                 // the next automatic refresh
  await page.clock.resume();                                       // then let time flow normally
  await expect(page.getByRole("alert").filter({ hasText: OLD })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();   // old data kept
});

test("the platform app says its overview is old when a refresh fails", async ({ page }) => {
  await signIn(page, "platform", PLATFORM.asker, PLATFORM.org, PLATFORM.password);
  await page.getByRole("button", { name: "Customers", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
  await expect(page.getByText(OLD)).toHaveCount(0);

  await page.route("**/api/v1/platform/overview", (route) => route.abort());
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByRole("alert").filter({ hasText: OLD })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();     // old data kept
});
