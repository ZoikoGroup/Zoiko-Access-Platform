import { expect, test } from "@playwright/test";
import { addFreeTimes, cancelPatientsBookings, LOGINS, patientBooksViaApi, SERVICE, signIn, staffConfirms } from "../helpers";

// Robot 3: a booking with a doctor appears on that doctor's schedule only once the clinic has confirmed it.
test.beforeEach(async ({ request }) => {
  await cancelPatientsBookings(request);
  await addFreeTimes(request, 2, true);
});
test.afterEach(async ({ request }) => { await cancelPatientsBookings(request); });

test("the doctor sees a booking only after the clinic confirms it", async ({ page, request }) => {
  const { serviceId } = await addFreeTimes(request, 1, true);
  const id = await patientBooksViaApi(request, serviceId, true);

  await signIn(page, "doctor", LOGINS.doctor);
  await page.getByRole("button", { name: "My schedule" }).first().click();
  const mine = page.locator(".appt", { hasText: SERVICE });
  await expect(page.getByText(/Upcoming \(0\)/)).toBeVisible();      // still waiting: not on the schedule
  await expect(mine).toHaveCount(0);

  await staffConfirms(request, id);
  await page.reload();
  await page.getByRole("button", { name: "My schedule" }).first().click();
  await expect(mine.filter({ hasText: "Confirmed" })).toHaveCount(1);
});
