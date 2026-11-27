import { expect, test } from "@playwright/test";
import { LOGINS, ORG, PARTNER_LOGINS, PASSWORD, PLATFORM, signIn, type APPS } from "../helpers";

// Robot 22: every kind of login signs in and lands on its own home screen with no error, and a picture
// of each home screen is kept in frontend/e2e/screens (not committed). One robot, one login at a time.
type Login = { role: string; name: string; app: keyof typeof APPS; org: string; login: string; password: string };
const LOGIN_LIST: Login[] = [
  { role: "R01", name: "patient", app: "patient", org: ORG, login: LOGINS.patient, password: PASSWORD },
  { role: "R03", name: "doctor", app: "doctor", org: ORG, login: LOGINS.doctor, password: PASSWORD },
  { role: "R04", name: "receptionist", app: "staff", org: ORG, login: "e2e.reception@example.org", password: PASSWORD },
  { role: "R13", name: "clinic administrator", app: "staff", org: ORG, login: LOGINS.admin, password: PASSWORD },
  { role: "R11", name: "regional support", app: "platform", org: PLATFORM.org, login: PLATFORM.asker, password: PLATFORM.password },
  { role: "R12", name: "clinical safety officer", app: "platform", org: PLATFORM.org, login: "iyer@zoiko.example", password: PLATFORM.password },
  ...PARTNER_LOGINS.map((p) => ({ role: p.role, name: p.name, app: "partners" as const, org: p.org, login: p.login, password: PASSWORD })),
];

for (const l of LOGIN_LIST) {
  test(`${l.role} ${l.name} signs in and lands on their own home screen`, async ({ page }) => {
    await signIn(page, l.app, l.login, l.org, l.password);
    await expect(page.locator(".hero h1").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".nav-item").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".error")).toHaveCount(0);                 // nothing refused or broken
    await page.screenshot({ path: `screens/${l.role}-${l.name.replace(/\s+/g, "-")}.png`, fullPage: true });
  });
}
