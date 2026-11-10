import { expect, test } from "@playwright/test";
import { addFreeTimes, cancelPatientsBookings, LOGINS, SERVICE, signIn } from "../helpers";

// Robot 1: a patient signs in, finds a service, books a free time and sees it waiting for the clinic.
test.beforeEach(async ({ request }) => {
  await cancelPatientsBookings(request);        // start clean
  await addFreeTimes(request);                  // something to book
});
test.afterEach(async ({ request }) => {
  await cancelPatientsBookings(request);        // leave nothing behind
});

test("a patient books an appointment and sees it waiting for the clinic", async ({ page }) => {
  await signIn(page, "patient", LOGINS.patient);
  await expect(page.getByRole("heading", { name: /Welcome back/ })).toBeVisible();

  await page.getByRole("button", { name: "Find Healthcare" }).first().click();
  await page.locator("button.service", { hasText: SERVICE }).click();
  await expect(page.getByText(`Free times for ${SERVICE}`)).toBeVisible();

  await page.locator("button.slot").first().click();
  await page.getByRole("button", { name: "Confirm booking" }).click();
  await expect(page.getByText("It is not confirmed until the clinic confirms it.")).toBeVisible();

  await page.getByRole("button", { name: "View appointments" }).click();
  const card = page.locator(".appt", { hasText: SERVICE }).filter({ hasText: "Waiting for the clinic" });
  await expect(card).toHaveCount(1);
  await expect(card.getByText("Waiting for the clinic")).toBeVisible();
});
