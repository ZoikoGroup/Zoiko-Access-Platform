import { expect, test } from "@playwright/test";
import { LOGINS, signIn } from "../helpers";

// Robot 7: the clinic administrator proposes an AI permit, moves it one step, and retires it with a reason.
// Nothing here switches AI on: the permit never leaves the early steps.
test("an administrator proposes, advances and retires an AI permit", async ({ page }) => {
  const feature = `robot.test_${Date.now()}`;
  await signIn(page, "staff", LOGINS.admin);
  await page.getByRole("button", { name: "AI permits" }).click();
  await expect(page.getByText(/AI is switched off/)).toBeVisible();

  await page.getByLabel("Feature id").fill(feature);
  await page.getByLabel("Purpose").fill("Robot test: draft a reply");
  await page.getByLabel("Model name and version").fill("template v1");
  await page.getByLabel("Prompt").fill("Write a polite holding reply");
  await page.getByRole("button", { name: "Propose", exact: true }).click();
  await expect(page.getByText("Permit proposed. It is not switched on.")).toBeVisible();

  const card = page.locator(".appt", { hasText: feature });
  await expect(card.getByText("Proposed")).toBeVisible();
  await card.getByRole("button", { name: "Move to Qualification" }).click();
  await expect(card.getByText("Qualification").first()).toBeVisible();

  const retire = card.getByRole("button", { name: "Retire for good" });
  await expect(retire).toBeDisabled();                                    // a reason is required
  await card.getByLabel(`Reason to retire ${feature}`).fill("robot cleanup");
  await retire.click();
  await expect(card.getByText("Retired", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: /Move to/ })).toHaveCount(0);   // final
});

test("the patient app has no AI permits menu", async ({ page }) => {
  await signIn(page, "patient", LOGINS.patient);
  await expect(page.getByRole("button", { name: "AI permits" })).toHaveCount(0);
});
