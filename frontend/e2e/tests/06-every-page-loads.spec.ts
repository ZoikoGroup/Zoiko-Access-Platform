import { expect, test, type Page } from "@playwright/test";
import { LOGINS, PLATFORM, signIn } from "../helpers";

// Robot 6: signs in to each app and opens EVERY page in its menu. A page fails the robot if the app
// shows its "Something went wrong" screen, throws an error, logs a browser error, or the server answers
// with an error (5xx). It checks that pages load, not that every number on them is right.
const APPS = [
  { name: "staff console", app: "staff" as const, login: LOGINS.admin },
  { name: "patient app", app: "patient" as const, login: LOGINS.patient },
  { name: "doctor app", app: "doctor" as const, login: LOGINS.doctor },
  { name: "platform app", app: "platform" as const, login: PLATFORM.asker, org: PLATFORM.org, password: PLATFORM.password },
];

function watch(page: Page) {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text().slice(0, 160)}`); });
  page.on("response", (r) => { if (r.status() >= 500) problems.push(`server ${r.status()}: ${r.url()}`); });
  return problems;
}

for (const a of APPS) {
  test(`every page in the ${a.name} loads`, async ({ page }) => {
    const problems = watch(page);
    await signIn(page, a.app, a.login, a.org, a.password);
    const items = page.locator(".nav-item");
    await expect(items.first()).toBeVisible();
    const labels = (await items.allInnerTexts()).map((t) => t.split("\n")[0].trim());
    expect(labels.length, "menu items").toBeGreaterThan(2);
    for (let i = 0; i < labels.length; i++) {
      await items.nth(i).click();
      await page.waitForLoadState("networkidle");
      await expect(page.getByText("Something went wrong on this screen"), `page "${labels[i]}"`).toHaveCount(0);
      await expect(page.locator("main, .main, .content").first(), `page "${labels[i]}" shows content`).toBeVisible();
    }
    expect(problems, `problems while opening ${labels.join(", ")}`).toEqual([]);
  });
}
