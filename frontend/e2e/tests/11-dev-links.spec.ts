import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

// Robot 11: every "Open signed in" button on the local launcher page (scripts/dev_links.py) really lands
// signed in, and the password is taken out of the address bar. Development only.
const root = path.resolve(__dirname, "../../..");
execSync("python scripts/dev_links.py --no-open", { cwd: root });
const page_html = readFileSync(path.join(root, "dev-links.html"), "utf8");
const links = [...page_html.matchAll(/href="([^"]*dev_pw=[^"]*)"/g)].map((m) => m[1].replace(/&amp;/g, "&"));

test("the launcher page has a direct-login link for every demo account", () => {
  expect(links.length).toBeGreaterThanOrEqual(10);
});

for (const url of links) {
  const who = new URL(url).searchParams.get("dev_login");
  test(`direct login works: ${who}`, async ({ page }) => {
    await page.goto(url);
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    expect(page.url()).not.toContain("dev_pw");                       // the password left the address bar
    expect(page.url()).not.toContain("dev_login");
  });
}
