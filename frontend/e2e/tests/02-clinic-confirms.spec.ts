import { expect, test } from "@playwright/test";
import { addFreeTimes, cancelPatientsBookings, LOGINS, patientBooksViaApi, SERVICE, signIn } from "../helpers";

// Robot 2: the clinic confirms a waiting appointment; the patient then sees "Confirmed".
test.beforeEach(async ({ request }) => {
  await cancelPatientsBookings(request);
  const { serviceId } = await addFreeTimes(request);
  await patientBooksViaApi(request, serviceId);
});
test.afterEach(async ({ request }) => { await cancelPatientsBookings(request); });

test("clinic staff confirm and the patient sees it", async ({ page, browser }) => {
  await signIn(page, "staff", LOGINS.admin);
  await page.getByRole("button", { name: "Appointments", exact: true }).click();
  const row = page.locator("tr", { hasText: SERVICE }).filter({ hasText: "Needs confirmation" });
  await row.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.locator("tr", { hasText: SERVICE }).getByText("Confirmed")).toBeVisible();

  const patient = await (await browser.newContext()).newPage();
  await signIn(patient, "patient", LOGINS.patient);
  await patient.getByRole("button", { name: "Appointments", exact: true }).first().click();
  await expect(patient.locator(".appt", { hasText: SERVICE }).getByText("Confirmed")).toBeVisible();
});
