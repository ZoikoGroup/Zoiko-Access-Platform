import { expect, test } from "@playwright/test";
import { APPS, LOGINS, PARTNER_LOGINS, signIn } from "../helpers";

// Robot 21: the whole journey of a request between separate customers, through the screens. The
// clinic sends it, the lab answers it, a doctor of the clinic marks the result as reviewed, and the
// clinic sees every step. Only an order number, a short description and the urgency reach the lab.
test("a clinic sends a lab order, the lab delivers a result and a doctor reviews it", async ({ page }) => {
  const lab = PARTNER_LOGINS[0];
  const token = `RB-${Date.now()}`;
  const label = `Robot blood count ${Date.now()}`;

  // 1. the clinic's staff send it
  await signIn(page, "staff", LOGINS.admin);
  await page.locator(".nav-item", { hasText: "Partner requests" }).click();
  await page.getByLabel("Partner", { exact: false }).first().selectOption({ label: lab.org });
  await page.getByLabel(/Order number/).fill(token);
  await page.getByLabel(/What is asked/).fill(label);
  await page.getByRole("button", { name: "Send request" }).click();
  const sent = page.locator(".appt", { hasText: label });
  await expect(sent).toBeVisible();
  await expect(sent.getByText("Sent, waiting for the partner")).toBeVisible();

  // 2. the lab answers it, and is never told who the patient is
  await signIn(page, "partners", lab.login, lab.org);
  await page.locator(".nav-item", { hasText: "Incoming requests" }).click();
  const incoming = page.locator(".appt", { hasText: label });
  await expect(incoming).toBeVisible();
  await expect(incoming).not.toContainText(/patient/i);
  await incoming.getByRole("button", { name: "Accept" }).click();
  await page.locator(".appt", { hasText: label }).getByRole("button", { name: "Start work" }).click();
  await page.locator(".appt", { hasText: label }).getByLabel(`Result reference for ${token}`).fill(`RES-${token}`);
  await page.locator(".appt", { hasText: label }).getByRole("button", { name: "Mark the result as available" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Delivered")).toBeVisible();

  // 2b. the patient sees it is ready, and that a ready result is not a doctor's review
  await signIn(page, "patient", LOGINS.patient);
  await page.locator(".nav-item", { hasText: "My Tests & Requests" }).click();
  const mine = page.locator(".appt", { hasText: label });
  await expect(mine.getByText("Result ready", { exact: true })).toBeVisible();
  await expect(mine).toContainText("Your doctor has not reviewed it yet");
  await expect(mine).not.toContainText(token);                       // never the order number

  // 3. a doctor of the clinic reviews it: delivery was not review
  await signIn(page, "doctor", LOGINS.doctor);
  await page.locator(".nav-item", { hasText: "Partner results" }).click();
  const ready = page.locator(".appt", { hasText: label });
  await expect(ready.getByText("Result ready: waiting for you")).toBeVisible();
  await ready.getByRole("button", { name: "I have reviewed this result" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Reviewed", { exact: true })).toBeVisible();

  // 3b. the patient now sees that their doctor reviewed it
  await page.goto(`${APPS.patient}/`);
  await page.locator(".nav-item", { hasText: "My Tests & Requests" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Reviewed by your doctor")).toBeVisible();

  // 3c. and was told, once, in their notifications (the event worker delivers it within moments)
  await page.locator(".nav-item", { hasText: "Notifications" }).click();
  await expect(page.getByText("Your doctor reviewed a result").first()).toBeVisible({ timeout: 30_000 });

  // 4. the clinic sees that a doctor reviewed it (its staff session is still open in this browser)
  await page.goto(`${APPS.staff}/`);
  await page.locator(".nav-item", { hasText: "Partner requests" }).click();
  await expect(page.locator(".appt", { hasText: label }).getByText("Reviewed by a doctor")).toBeVisible();
});

test("the lab's home screen counts its requests", async ({ page }) => {
  const lab = PARTNER_LOGINS[0];
  await signIn(page, "partners", lab.login, lab.org);
  await expect(page.getByLabel("Your numbers").getByText("New requests")).toBeVisible();
});
