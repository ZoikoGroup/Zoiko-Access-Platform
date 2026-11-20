import { expect, test } from "@playwright/test";
import { addFreeTimes, cancelPatientsBookings, LOGINS, patientBooksViaApi, SERVICE, signIn } from "../helpers";

// Robot 4: the patient moves an appointment, then cancels it with a reason; History shows both steps.
test.beforeEach(async ({ request }) => {
  await cancelPatientsBookings(request);
  const { serviceId } = await addFreeTimes(request, 3);
  await patientBooksViaApi(request, serviceId);
});
test.afterEach(async ({ request }) => { await cancelPatientsBookings(request); });

test("a patient moves then cancels, and History tells the story", async ({ page }) => {
  await signIn(page, "patient", LOGINS.patient);
  await page.getByRole("button", { name: "Appointments", exact: true }).first().click();
  const all = page.locator(".appt");
  await expect(all.first()).toBeVisible();
  const cancelledBefore = await page.locator(".appt", { hasText: "Cancelled" }).count();   // earlier runs leave some
  const card = page.locator(".appt", { hasText: SERVICE }).filter({ hasNotText: "Cancelled" });
  await expect(card).toHaveCount(1);

  await card.getByRole("button", { name: "Change time" }).click();
  await card.locator("button.slot").first().click();
  await expect(card.getByText("Moved from an earlier time.")).toBeVisible();

  // The History shows the move before anything else happens.
  await card.getByRole("button", { name: "History" }).click();
  await expect(card.getByText("Booked as the new time")).toBeVisible();

  // Cancel with a reason: the card leaves "Upcoming" and one more cancelled card appears.
  await card.getByRole("button", { name: "Cancel", exact: true }).click();
  await card.getByLabel("Why are you cancelling? (optional)").selectOption({ index: 1 });
  await card.getByRole("button", { name: "Yes, cancel it" }).click();
  await expect(page.locator(".appt", { hasText: "Cancelled" })).toHaveCount(cancelledBefore + 1);
  await expect(page.locator(".appt", { hasText: SERVICE }).filter({ hasNotText: "Cancelled" })).toHaveCount(0);
});
