import { expect, test } from "@playwright/test";
import { addFreeTimes, cancelPatientsBookings, clearNameCheck, doctorSetsName, LOGINS, SERVICE, signIn } from "../helpers";

// Robot 8: the administrator records that a doctor's name was checked; a patient then sees "Verified"
// next to that doctor's free times; withdrawing the check removes the mark.
const NAME = "Dr Robot Tester";

test.beforeEach(async ({ request }) => {
  await cancelPatientsBookings(request);
  await addFreeTimes(request, 2, true);
  await doctorSetsName(request, NAME);
  await clearNameCheck(request);                       // start with no check
});
test.afterEach(async ({ request }) => {
  await clearNameCheck(request);
  await cancelPatientsBookings(request);
});

test("a checked doctor shows Verified to patients, and withdrawing removes it", async ({ page, browser }) => {
  await signIn(page, "staff", LOGINS.admin);
  await page.getByRole("button", { name: "Providers" }).click();
  const row = page.locator("tr", { hasText: NAME });
  await expect(row.getByText("Verified", { exact: true })).toHaveCount(0);       // not checked yet (or withdrawn)
  await row.getByRole("button", { name: "Record a check" }).click();
  await row.getByLabel(`Note for ${NAME}`).fill("Robot test: register number seen");
  await row.getByRole("button", { name: "Save check" }).click();
  await expect(page.getByText("Check recorded.")).toBeVisible();
  await expect(row.getByText("Verified", { exact: true })).toBeVisible();

  const patient = await (await browser.newContext()).newPage();
  const seesMark = async () => {
    await patient.getByRole("button", { name: "Home", exact: true }).click();          // a reload would sign out
    await patient.getByRole("button", { name: "Find Healthcare" }).first().click();
    await patient.locator("button.service", { hasText: SERVICE }).click();
    await expect(patient.getByText(`Free times for ${SERVICE}`)).toBeVisible();
    const slot = patient.locator("button.slot", { hasText: NAME }).first();
    await expect(slot).toBeVisible();
    return (await slot.getByText("Verified", { exact: true }).count()) > 0;
  };
  await signIn(patient, "patient", LOGINS.patient);
  await expect(patient.getByRole("button", { name: "Home", exact: true })).toBeVisible();   // signed in
  expect(await seesMark(), "patient sees the mark").toBe(true);

  // The doctor sees the same state on their own account page.
  const doctor = await (await browser.newContext()).newPage();
  await signIn(doctor, "doctor", LOGINS.doctor);
  await doctor.getByRole("button", { name: "My account" }).click();
  await expect(doctor.getByText("Is my name checked?")).toBeVisible();
  await expect(doctor.getByText(/Patients see a Verified mark next to it/)).toBeVisible();

  await row.getByRole("button", { name: "Withdraw the check" }).click();
  await row.getByLabel(`Reason to withdraw the check for ${NAME}`).fill("robot cleanup");
  await row.getByRole("button", { name: "Withdraw", exact: true }).click();
  await expect(page.getByText("Check withdrawn.")).toBeVisible();
  expect(await seesMark(), "the mark is gone after withdrawing").toBe(false);
});
