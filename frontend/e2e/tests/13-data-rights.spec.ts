import { expect, test } from "@playwright/test";
import { LOGINS, signIn } from "../helpers";

// Robot 13: a patient asks the clinic about their own data; the clinic checks who is asking and an
// administrator closes it; the patient sees the outcome.
const NOTE = "Robot: please send me a copy";
const OUTCOME = "A copy was sent to you by post";

test("a data request goes from the patient to the clinic and back", async ({ page, browser }) => {
  const patient = await (await browser.newContext()).newPage();
  await signIn(patient, "patient", LOGINS.patient);
  await patient.getByRole("button", { name: /Privacy & Consent/ }).first().click();
  const withdraw = patient.getByRole("button", { name: "Withdraw" });
  if (await withdraw.count()) {                                     // clear a leftover from a failed run
    await withdraw.first().click();
    await expect(patient.getByText("Withdrawn").first()).toBeVisible();
  }

  await patient.getByLabel(/What do you want/).selectOption("access");
  await patient.getByLabel(/Anything the clinic should know/).fill(NOTE);
  await patient.getByRole("button", { name: "Send the request" }).click();
  await expect(patient.getByText(/Your reference is DSR-/)).toBeVisible();
  await expect(patient.getByText("Received").first()).toBeVisible();

  const staff = page;
  await signIn(staff, "staff", LOGINS.admin);
  await staff.getByRole("button", { name: "Data requests", exact: true }).click();
  await expect(staff.getByText(`Their words: ${NOTE}`).first()).toBeVisible();
  const mark = staff.getByRole("button", { name: "Mark done" }).first();
  await expect(mark).toBeDisabled();                                // not before the identity check
  await staff.getByRole("button", { name: "I have checked who is asking" }).first().click();
  await expect(mark).toBeEnabled();
  await staff.getByLabel(/Note to the patient for DSR-/).first().fill(OUTCOME);
  await mark.click();
  await expect(staff.getByText(`Told the patient: ${OUTCOME}`).first()).toBeVisible();

  await patient.reload();
  await patient.getByRole("button", { name: /Privacy & Consent/ }).first().click();
  await expect(patient.getByText("Done").first()).toBeVisible();
  await expect(patient.getByText(`The clinic says: ${OUTCOME}`).first()).toBeVisible();
  await expect(patient.getByText("The clinic has checked who you are.").first()).toBeVisible();
});
