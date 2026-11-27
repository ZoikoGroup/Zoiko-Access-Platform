import { expect, test } from "@playwright/test";
import { LOGINS, patientInsurance, signIn } from "../helpers";

// Robot 9: clinic staff see a patient's insurance only after the patient shares it, and lose sight of it
// again when the patient takes the choice back.
const INSURER = "Robot Insure Ltd";

test.beforeEach(async ({ request }) => {
  const mine = await patientInsurance(request);
  await mine.clear();
  await mine.add(INSURER);
});
test.afterEach(async ({ request }) => { await (await patientInsurance(request)).clear(); });

test("staff see insurance only while the patient shares it", async ({ page, browser }) => {
  const staff = page;
  await signIn(staff, "staff", LOGINS.admin);
  const openPatient = async () => {
    await staff.getByRole("button", { name: "Dashboard", exact: true }).click();
    await staff.getByRole("button", { name: "Patients", exact: true }).click();
    await staff.getByRole("button", { name: /^(Edit|Add details)$/ }).first().click();
    await expect(staff.getByText("Insurance", { exact: true }).first()).toBeVisible();
  };
  const notice = staff.getByText(/has not chosen to share their insurance details/);

  await openPatient();
  await expect(notice).toBeVisible();                                   // the cautious default
  await expect(staff.getByText(INSURER)).toHaveCount(0);

  const patient = await (await browser.newContext()).newPage();
  await signIn(patient, "patient", LOGINS.patient);
  await patient.getByRole("button", { name: /Insurance/ }).first().click();
  await expect(patient.getByText(INSURER)).toBeVisible();               // the patient always sees their own
  const box = patient.getByLabel("Let the clinic's staff see my insurance details");
  await expect(box).not.toBeChecked();
  await box.click();                                                    // updates after the server answers
  await expect(box).toBeChecked();

  await openPatient();
  await expect(staff.getByText(INSURER)).toBeVisible();
  await expect(notice).toHaveCount(0);

  await box.click();
  await expect(box).not.toBeChecked();
  await openPatient();
  await expect(notice).toBeVisible();
  await expect(staff.getByText(INSURER)).toHaveCount(0);
});
