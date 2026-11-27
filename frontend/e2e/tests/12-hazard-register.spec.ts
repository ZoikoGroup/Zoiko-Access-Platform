import { expect, test } from "@playwright/test";
import { PLATFORM, SAFETY, signIn } from "../helpers";

// Robot 12: one safety officer assesses a hazard and proposes a risk control tied to a test; a DIFFERENT one
// reviews the hazard, verifies the control and accepts the remaining risk (DEV-382: never without a
// verified control), and the first cannot accept their own work. Uses H20 and ends by assessing it again
// and withdrawing the control, so it can be run again and again (a retired hazard could not be reused).
test("a hazard is assessed by one safety officer and verified and accepted by another", async ({ browser }) => {
  const first = await (await browser.newContext()).newPage();
  const second = await (await browser.newContext()).newPage();
  const open = async (page: typeof first, who: string) => {
    await signIn(page, "platform", who, PLATFORM.org, PLATFORM.password);
    await page.getByRole("button", { name: "Hazards", exact: true }).click();
    await page.getByRole("button", { name: "Open H20" }).click();
    return page;
  };

  await open(first, SAFETY.first);
  await first.getByLabel("How you judged it").fill("Browser test: judged against the audit log checks");
  await first.getByRole("button", { name: "Save assessment" }).click();
  await expect(first.getByText(/Assessment saved/)).toBeVisible();
  await expect(first.locator(".appt", { hasText: "H20" }).getByText(/Assessed by/)).toBeVisible();

  // A risk control, tied to a test that exists in this build.
  await first.getByLabel("What the control does").fill("Browser test: the audit chain is checked on every read");
  await first.getByLabel("Where it lives").fill("backend/modules/evidence/audit.py");
  await first.getByLabel("The test that proves it").fill("tests/test_hazards.py::test_retiring_needs_a_reason_and_is_final");
  await first.getByRole("button", { name: "Propose control" }).click();
  await expect(first.getByText(/Control proposed/)).toBeVisible();

  // The same person cannot review or accept their own assessment.
  await first.getByRole("button", { name: "Verify (independent review)" }).click();
  await expect(first.getByRole("alert")).toContainText("cannot do that");

  await open(second, SAFETY.second);
  await second.getByRole("button", { name: "Verify (independent review)" }).click();
  await expect(second.getByText("Verified.")).toBeVisible();
  await second.getByLabel(/^Note for C-\d+$/).fill("Browser test: ran the named test on this build");
  await second.getByRole("button", { name: /^Verify C-\d+$/ }).click();
  await expect(second.getByText(/C-\d+ verified\./)).toBeVisible();
  await second.getByRole("button", { name: "Accept the remaining risk" }).click();
  await expect(second.getByText("Remaining risk accepted.")).toBeVisible();
  await expect(second.locator(".appt", { hasText: "H20" }).getByText("Risk accepted")).toBeVisible();

  // Tidy up: the first officer assesses again. A new judgment cancels the sign-off, so the next run starts clean.
  await first.getByRole("button", { name: "Save assessment" }).click();
  await expect(first.getByText(/Assessment saved/)).toBeVisible();
  await expect(first.locator(".appt", { hasText: "H20" }).getByText("Open", { exact: true }).first()).toBeVisible();
  await first.getByLabel(/^Note for C-\d+$/).fill("Browser test tidy-up");
  await first.getByRole("button", { name: /^Withdraw C-\d+$/ }).click();
  await expect(first.getByText(/C-\d+ withdrawn\./)).toBeVisible();
});
