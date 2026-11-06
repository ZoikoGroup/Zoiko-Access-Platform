import { expect, test, type Page } from "@playwright/test";
import { clinicAdminCanSignIn, ORG, PLATFORM, signIn } from "../helpers";

// Robot 5: one support person asks to suspend a customer, a DIFFERENT one approves, the customer's
// staff are then refused, and the same two-person rule brings them back.
const signedIn = new WeakSet<Page>();            // each browser window signs in once, then just reloads
async function openCustomer(page: Page, who: string) {
  if (signedIn.has(page)) await page.reload();
  else { await signIn(page, "platform", who, PLATFORM.org, PLATFORM.password); signedIn.add(page); }
  await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  await page.waitForLoadState("networkidle");      // let the lists finish loading before clicking
  return page.locator(".appt", { hasText: ORG });
}

async function ask(page: Page, who: string, button: RegExp, reason: string) {
  const row = await openCustomer(page, who);
  await row.getByRole("button", { name: button }).click();
  await row.getByLabel("Reason").fill(reason);
  await row.getByRole("button", { name: "Send request" }).click();
  await expect(page.getByText("Request sent. A second person must approve it.")).toBeVisible();
  await expect(row.getByText(/requested by/)).toBeVisible();
  await expect(row.getByRole("button", { name: "Review this request" })).toHaveCount(0);   // not for the asker
  await expect(row.getByRole("button", { name: "Take my request back" })).toBeVisible();
}

async function approve(page: Page, who: string, message: string) {
  const row = await openCustomer(page, who);
  await row.getByRole("button", { name: "Review this request" }).click();
  await row.getByRole("button", { name: "Approve and apply" }).click();
  await expect(page.getByText(message)).toBeVisible();
}

test.use({ actionTimeout: 15_000 });
test.setTimeout(120_000);
test("two support people suspend a customer and bring it back", async ({ browser, request }) => {
  const first = await (await browser.newContext()).newPage();
  const second = await (await browser.newContext()).newPage();
  if (!(await clinicAdminCanSignIn(request))) {          // a crashed earlier run left it suspended: repair
    const row = await openCustomer(second, PLATFORM.approver);
    if (await row.getByRole("button", { name: "Review this request" }).count() === 0) {
      await ask(first, PLATFORM.asker, /Ask to reinstate/, "Robot repair");
    }
    await approve(second, PLATFORM.approver, "Customer reinstated.");
  }
  expect(await clinicAdminCanSignIn(request), "the clinic starts active").toBe(true);
  let suspended = false;
  try {
    await ask(first, PLATFORM.asker, /Ask to suspend/, "Robot test: safety check");
    expect(await clinicAdminCanSignIn(request), "asking alone changes nothing").toBe(true);

    await approve(second, PLATFORM.approver, "Customer suspended.");
    suspended = true;
    expect(await clinicAdminCanSignIn(request), "suspended staff are refused").toBe(false);
    await expect(second.locator(".appt", { hasText: ORG }).getByText("Suspended", { exact: true })).toBeVisible();

    await ask(first, PLATFORM.asker, /Ask to reinstate/, "Robot test: done");
    await approve(second, PLATFORM.approver, "Customer reinstated.");
    suspended = false;
    expect(await clinicAdminCanSignIn(request), "reinstated staff get in again").toBe(true);
  } finally {
    if (suspended) {                                   // never leave the test clinic locked out
      await ask(first, PLATFORM.asker, /Ask to reinstate/, "Robot cleanup").catch(() => undefined);
      await approve(second, PLATFORM.approver, "Customer reinstated.").catch(() => undefined);
    }
  }
});
