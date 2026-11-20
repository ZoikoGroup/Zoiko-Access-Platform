import { expect, test } from "@playwright/test";
import { LOGINS, SERVICE, signIn } from "../helpers";

// Robot 16: the clinic administrator sets a price, staff make, issue and mark an invoice as paid with
// the reference of a payment taken elsewhere, and the patient sees it as paid in their own app.
test("a bill goes from the clinic to the patient", async ({ page, browser }) => {
  await signIn(page, "staff", LOGINS.admin);
  await page.getByRole("button", { name: "Billing", exact: true }).click();

  await page.getByLabel(`New price for ${SERVICE}`).fill("45.00");
  await page.getByRole("button", { name: "Set price" }).first().click();
  await expect(page.getByText(/45\.00 from /).first()).toBeVisible();

  const patient = page.locator("label", { hasText: /^Patient/ }).locator("select");
  const options = await patient.locator("option").allTextContents();
  const mine = options.findIndex((t) => /e2e/i.test(t));
  await patient.selectOption({ index: mine > 0 ? mine : 1 });
  await page.locator("label", { hasText: /^Service/ }).locator("select").selectOption({ label: SERVICE });
  await page.getByRole("button", { name: "Make the invoice" }).click();

  const row = page.locator(".appt").filter({ hasText: /INV-\d+/ }).filter({ hasText: "Draft" }).first();
  await expect(row).toBeVisible();
  const number = (await row.getByText(/INV-\d+/).first().textContent())!.match(/INV-\d+/)![0];
  await row.getByRole("button", { name: "Issue" }).click();

  const issued = page.locator(".appt").filter({ hasText: number });
  await expect(issued.getByText("Issued")).toBeVisible();
  await issued.getByLabel(/Payment reference for/).fill("robot-pay-0001");
  await issued.getByRole("button", { name: "Mark as paid" }).click();
  await expect(issued.getByText("Marked as paid")).toBeVisible();

  const mineApp = await (await browser.newContext()).newPage();
  await signIn(mineApp, "patient", LOGINS.patient);
  await mineApp.getByRole("button", { name: /Bills & Payments/ }).first().click();
  const bill = mineApp.locator(".appt").filter({ hasText: number });
  await expect(bill.getByText("Paid")).toBeVisible();
  await expect(bill.getByText(new RegExp(`1 x ${SERVICE}`))).toBeVisible();
  await expect(mineApp.getByRole("button", { name: /^Pay/ })).toHaveCount(0);   // nothing to pay online
});
