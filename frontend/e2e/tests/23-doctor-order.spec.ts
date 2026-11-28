import { expect, test } from "@playwright/test";
import { LOGINS, PARTNER_LOGINS, signIn } from "../helpers";

// Robot 23: a doctor places an order from the doctor app. It appears in their list, waiting for the
// service; the order says only what is asked, and tells the doctor the patient must agree first.
test("a doctor places a lab order for their own patient", async ({ page }) => {
  const lab = PARTNER_LOGINS[0];
  const label = `Robot order ${Date.now()}`;
  await signIn(page, "doctor", LOGINS.doctor);
  await page.locator(".nav-item", { hasText: "Orders & results" }).click();
  await expect(page.getByRole("heading", { name: "Place an order" })).toBeVisible();
  await page.getByLabel("Order patient").selectOption({ index: 1 });
  await page.getByLabel("Order service").selectOption({ label: lab.org });
  await page.getByLabel("What is asked").fill(label);
  await page.getByRole("button", { name: "Send order" }).click();
  await expect(page.getByText(/asked to agree to share their name/)).toBeVisible();
  const row = page.locator(".appt", { hasText: label });
  await expect(row).toBeVisible();
  await expect(row.getByText("Sent, waiting for the partner")).toBeVisible();

  // tidy up: an order left open would later stop a customer being suspended without a plan, so the
  // clinic cancels it
  await signIn(page, "staff", LOGINS.admin);
  await page.locator(".nav-item", { hasText: "Partner requests" }).click();
  await page.locator(".appt", { hasText: label }).getByRole("button", { name: "Cancel the request" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Cancelled", { exact: true })).toBeVisible();
});
