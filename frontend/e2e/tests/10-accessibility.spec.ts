import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { LOGINS, PLATFORM, signIn } from "../helpers";

// Robot 10: an automated accessibility scan (WCAG 2.2 AA rules) of every page in every app.
// Automated rules find only part of the problems (roughly a third); a person with a screen reader and a
// keyboard still has to test (docs: GHE-15). The robot fails on any SERIOUS or CRITICAL finding.
const APPS = [
  { name: "sign-in pages", app: "staff" as const, signedOut: true },
  { name: "staff console", app: "staff" as const, login: LOGINS.admin },
  { name: "patient app", app: "patient" as const, login: LOGINS.patient },
  { name: "doctor app", app: "doctor" as const, login: LOGINS.doctor },
  { name: "platform app", app: "platform" as const, login: PLATFORM.asker, org: PLATFORM.org, password: PLATFORM.password },
];
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const examples: string[] = [];

async function scan(page: Page, where: string, found: Map<string, Set<string>>) {
  const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  for (const v of result.violations.filter((x) => x.impact === "serious" || x.impact === "critical")) {
    const key = `${v.id} (${v.impact}): ${v.help}`;
    if (!found.has(key)) found.set(key, new Set());
    found.get(key)!.add(where);
    for (const n of v.nodes.slice(0, 3)) {
      const detail = `${v.id} @ ${n.target.join(" ")}: ${(n.any[0]?.message ?? n.failureSummary ?? "").slice(0, 150)}`;
      if (!examples.includes(detail) && examples.length < 14) examples.push(detail);
    }
  }
}

const report = (found: Map<string, Set<string>>) =>
  [...found.entries()].map(([k, pages]) => `${k}\n    on: ${[...pages].join(", ")}`).join("\n") +
  `\nExamples:\n  ${examples.join("\n  ")}`;

for (const a of APPS) {
  test(`accessibility: ${a.name}`, async ({ page, browser }) => {
    const found = new Map<string, Set<string>>();
    if (a.signedOut) {
      for (const app of ["staff", "patient", "doctor", "platform"] as const) {
        const ctx = await browser.newContext();
        const p = await ctx.newPage();
        await p.goto({ staff: "http://localhost:5173", patient: "http://localhost:5174", doctor: "http://localhost:5176", platform: "http://localhost:5175" }[app] + "/");
        await p.getByRole("button", { name: "Sign in", exact: true }).waitFor();
        await scan(p, `${app} sign-in`, found);
        await ctx.close();
      }
    } else {
      await signIn(page, a.app, a.login!, a.org, a.password);
      const items = page.locator(".nav-item");
      await expect(items.first()).toBeVisible();
      const labels = (await items.allInnerTexts()).map((t) => t.split("\n")[0].trim());
      for (let i = 0; i < labels.length; i++) {
        await items.nth(i).click();
        await page.waitForLoadState("networkidle");
        await scan(page, labels[i], found);
      }
    }
    expect(found.size, `Accessibility problems:\n${report(found)}`).toBe(0);
  });
}
