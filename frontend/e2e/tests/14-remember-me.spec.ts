import { expect, test, type Browser, type Page } from "@playwright/test";
import { APPS, LOGINS, ORG, PASSWORD } from "../helpers";

// Robot 14: "Remember me for 30 days" is only for patients. A remembered patient is still signed in
// on a fresh browser that kept only this device's storage; a patient who did not tick it is not.
async function patientSignIn(page: Page, remember: boolean) {
  await page.goto(`${APPS.patient}/`);
  await page.getByLabel("Organization").fill(ORG);
  await page.getByLabel("Email").fill(LOGINS.patient);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  const box = page.getByLabel("Remember me for 30 days");
  await expect(box).toBeEnabled();
  if (remember) await box.check();
  await page.getByRole("button", { name: "Log In", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

// A new browser that keeps this device's long-term storage but not the per-tab storage.
async function reopen(browser: Browser, page: Page): Promise<Page> {
  const saved = await page.context().storageState();     // cookies + localStorage only
  const next = await browser.newContext({ storageState: saved });
  const again = await next.newPage();
  await again.goto(`${APPS.patient}/`);
  return again;
}

test("a patient who ticks the box is still signed in on this device", async ({ browser }) => {
  const page = await (await browser.newContext()).newPage();
  await patientSignIn(page, true);
  const again = await reopen(browser, page);
  await expect(again.getByRole("button", { name: "Sign out" })).toBeVisible();
  // Signing out ends it everywhere, including the saved copy.
  await again.getByRole("button", { name: "Sign out" }).click();
  await expect(again.getByRole("button", { name: "Log In", exact: true })).toBeVisible();
  const third = await reopen(browser, again);
  await expect(third.getByRole("button", { name: "Log In", exact: true })).toBeVisible();
});

test("a patient who does not tick the box is signed out when the tab closes", async ({ browser }) => {
  const page = await (await browser.newContext()).newPage();
  await patientSignIn(page, false);
  const again = await reopen(browser, page);
  await expect(again.getByRole("button", { name: "Log In", exact: true })).toBeVisible();
});

test("staff cannot tick it", async ({ page }) => {
  await page.goto(`${APPS.staff}/`);
  await expect(page.getByLabel("Remember me for 30 days")).toBeDisabled();
});
