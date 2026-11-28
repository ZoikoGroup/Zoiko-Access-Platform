import { expect, test } from "@playwright/test";
import { APPS, LOGINS, PARTNER_LOGINS, signIn } from "../helpers";

// Robot 25: an employer enrols a member and makes a one-time code; the patient types it and agrees
// to be counted; the employer sees the member as linked and learns nothing else about them.
test("an employer invites a member, the patient joins with the code and can leave", async ({ page }) => {
  const employer = PARTNER_LOGINS.find((p) => p.org === "E2E Employer")!;
  const member = `rb-${Date.now()}`;

  await signIn(page, "partners", employer.login, employer.org);
  await page.locator(".nav-item", { hasText: "Programme" }).click();
  await page.getByPlaceholder("emp-0001, emp-0002").fill(member);
  await page.getByRole("button", { name: /^Enrol/ }).click();
  await expect(page.getByText(/\d+ enrolled/)).toBeVisible();
  await page.getByLabel("Member reference").fill(member);
  await page.getByRole("button", { name: "Make a one-time code" }).click();
  const shown = page.getByRole("status").filter({ hasText: "Code for" });
  await expect(shown).toBeVisible();
  const code = ((await shown.textContent()) ?? "").match(/[A-Z0-9]{5}-[A-Z0-9]{5}/)![0];

  await signIn(page, "patient", LOGINS.patient);
  await page.locator(".nav-item", { hasText: "Employer Programme" }).click();
  await page.getByPlaceholder("XXXXX-XXXXX").fill(code);
  await page.getByRole("button", { name: "I agree to be counted" }).click();
  await expect(page.getByText(/You are now counted in/)).toBeVisible();
  await expect(page.locator(".appt", { hasText: employer.org })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(member);          // never the employer's reference

  await page.goto(`${APPS.partners}/`);
  await page.locator(".nav-item", { hasText: "Programme" }).click();
  const row = page.locator(".appt", { hasText: member });
  await expect(row.getByText("Linked")).toBeVisible();

  await page.goto(`${APPS.patient}/`);
  await page.locator(".nav-item", { hasText: "Employer Programme" }).click();
  await page.locator(".appt", { hasText: employer.org }).getByRole("button", { name: "Leave" }).click();
  await expect(page.getByText("You are not in any programme.")).toBeVisible();
});
