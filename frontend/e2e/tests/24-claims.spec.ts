import { expect, test } from "@playwright/test";
import { APPS, LOGINS, PARTNER_LOGINS, signIn } from "../helpers";

// Robot 24: a clinic sends a claim to a payer, the payer sees only the member id, service, amount
// and date (never the patient), approves it and marks it paid; the clinic sees each step.
test("a clinic claims from a payer and the payer approves and pays", async ({ page }) => {
  const payer = PARTNER_LOGINS.find((p) => p.org === "E2E Payer")!;
  const label = `Robot consultation ${Date.now()}`;
  const member = `RB-${Date.now()}`;

  await signIn(page, "staff", LOGINS.admin);
  await page.locator(".nav-item", { hasText: "Claims" }).click();
  await page.getByLabel("Member id", { exact: false }).fill(member);
  await page.getByLabel(/What was done/).fill(label);
  await page.getByLabel(/Kind of service/).fill(`rb_${Date.now()}`);   // a fresh kind each run: no duplicate
  await page.getByLabel("Amount").fill("45.00");
  await page.getByRole("button", { name: "Send claim" }).click();
  const sent = page.locator(".appt", { hasText: label });
  await expect(sent).toBeVisible();
  await expect(sent.getByText("Sent, waiting for the payer")).toBeVisible();

  // the payer is told nothing until the patient agrees
  await signIn(page, "partners", payer.login, payer.org);
  await page.locator(".nav-item", { hasText: "Claims" }).click();
  await expect(page.locator(".appt", { hasText: label })).toHaveCount(0);

  // the patient sees exactly what would be sent and agrees
  await signIn(page, "patient", LOGINS.patient);
  await page.locator(".nav-item", { hasText: "My Claims" }).click();
  const ask = page.locator(".appt", { hasText: label });
  await expect(ask).toContainText("Your agreement is needed");
  await expect(ask).not.toContainText(member);
  await ask.getByRole("button", { name: "I agree to this claim" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Sent to your insurer")).toBeVisible();

  await page.goto(`${APPS.partners}/`);
  await page.locator(".nav-item", { hasText: "Claims" }).click();
  const row = page.locator(".appt", { hasText: label });
  await expect(row).toContainText(member);
  await expect(row).toContainText("45.00 GBP");
  await expect(row).not.toContainText(/patient/i);                   // never who the patient is
  await row.getByRole("button", { name: "Approve" }).click();
  await page.locator(".appt", { hasText: label }).getByLabel(/Payment reference for/).fill(`PAY-${Date.now()}`);
  await page.locator(".appt", { hasText: label }).getByRole("button", { name: "Mark as paid" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Paid", { exact: true })).toBeVisible();

  await page.goto(`${APPS.staff}/`);
  await page.locator(".nav-item", { hasText: "Claims" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Paid", { exact: true })).toBeVisible();
});
