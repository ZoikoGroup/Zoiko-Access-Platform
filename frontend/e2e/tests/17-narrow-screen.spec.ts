import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { LOGINS, PARTNER_LOGINS, PLATFORM, signIn } from "../helpers";

// Robot 17: each app on a 320 pixel wide screen (WCAG 2.2 reflow, GHE-15 and GHE-16 TC33). On every
// page nothing may force sideways scrolling and the automated accessibility rules must still pass;
// the menu must be reachable from the keyboard. A person with a phone or a magnified screen still
// has to try it: this finds the mechanical failures only.
test.use({ viewport: { width: 320, height: 640 } });

const APPS = [
  { name: "staff console", app: "staff" as const, login: LOGINS.admin },
  { name: "patient app", app: "patient" as const, login: LOGINS.patient },
  { name: "doctor app", app: "doctor" as const, login: LOGINS.doctor },
  { name: "platform app", app: "platform" as const, login: PLATFORM.asker, org: PLATFORM.org, password: PLATFORM.password },
  ...PARTNER_LOGINS.map((p) => ({
    name: `partner app as ${p.role}`, app: "partners" as const, login: p.login, org: p.org })),
];

for (const a of APPS) {
  test(`every page of the ${a.name} reflows at 320 pixels and keeps its accessibility`, async ({ page }) => {
    await signIn(page, a.app, a.login, a.org, a.password);
    const items = page.locator(".nav-item");
    await expect(items.first()).toBeVisible();
    const labels = (await items.allInnerTexts()).map((t) => t.split("\n")[0].trim());
    for (let i = 0; i < labels.length; i++) {
      await items.nth(i).click();
      await page.waitForLoadState("networkidle");
      const sideways = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(sideways, `page "${labels[i]}" scrolls sideways by ${sideways}px`).toBeLessThanOrEqual(1);
      const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
      const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.help}`), `page "${labels[i]}"`).toEqual([]);
    }
  });
}

test("the patient menu can be used from the keyboard at 320 pixels", async ({ page }) => {
  await signIn(page, "patient", LOGINS.patient);
  const items = page.locator(".nav-item");
  await expect(items.first()).toBeVisible();
  await items.first().focus();
  await expect(items.first()).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(items.nth(1)).toBeFocused();
  await page.keyboard.press("Enter");
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main, .main, .content").first()).toBeVisible();
});
