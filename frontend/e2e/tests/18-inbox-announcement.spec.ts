import { expect, test } from "@playwright/test";
import { LOGINS, signIn } from "../helpers";

// Robot 18: the patient inbox says, in a polite live region a screen reader reads aloud, how many
// new messages there are (GHE-16 TC34). It finds the mechanical part: a person with a screen reader
// must still try it.
test("the patient inbox announces its unread count in a polite live region", async ({ page }) => {
  await signIn(page, "patient", LOGINS.patient);
  await page.locator(".nav-item", { hasText: "Messages" }).click();
  const status = page.getByTestId("inbox-status");
  await expect(status).toHaveAttribute("role", "status");
  await expect(status).toHaveAttribute("aria-live", "polite");
  await expect(status).toHaveText(/^(No new messages\.|You have \d+ new messages?\.)$/);
});

test("the staff inbox announces how many conversations have new messages", async ({ page }) => {
  await signIn(page, "staff", LOGINS.admin);
  await page.locator(".nav-item", { hasText: "Messages" }).click();
  const status = page.getByTestId("inbox-status");
  await expect(status).toHaveAttribute("role", "status");
  await expect(status).toHaveAttribute("aria-live", "polite");
  await expect(status).toHaveText(/^(No conversations waiting\.|\d+ conversations? with new messages\.)$/);
});
