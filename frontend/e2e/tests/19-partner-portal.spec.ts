import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { LOGINS, PARTNER_LOGINS, signIn } from "../helpers";

// Robot 19: the partner app (GHE-15 roles R05 to R09). Each kind of login signs in, sees its own role
// named with what it may reach and what is not connected yet, passes the accessibility scan, and a
// lab, pharmacy or home-care partner can add and ask to list a service. The wrong kind of login is
// pointed to its own app and given nothing.
for (const p of PARTNER_LOGINS) {
  test(`${p.name} (${p.role}) signs in and sees an honest overview`, async ({ page }) => {
    await signIn(page, "partners", p.login, p.org);
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    await expect(page.getByText(p.name).first()).toBeVisible();
    await expect(page.getByText("Waiting for a decision")).toBeVisible();
    await expect(page.getByText("What this role never does")).toBeVisible();
    const items = (await page.locator(".nav-item").allInnerTexts()).map((t) => t.trim());
    expect(items.includes("My services")).toBe(p.services);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
  });
}

test("a lab adds a service and asks for it to be listed", async ({ page }) => {
  const lab = PARTNER_LOGINS[0];
  await signIn(page, "partners", lab.login, lab.org);
  await page.locator(".nav-item", { hasText: "My services" }).click();
  const name = `Robot blood test ${Date.now()}`;
  await page.getByLabel("Service name").fill(name);
  await page.getByLabel("Category").fill("diagnostics");
  await page.getByRole("button", { name: "Add service" }).click();
  const row = page.locator(".appt", { hasText: name });
  await expect(row).toBeVisible();
  await expect(row.getByText("Not listed")).toBeVisible();
  await row.getByLabel(`Evidence for listing ${name}`).fill("licence-0001");
  await row.getByRole("button", { name: "Ask for a listing" }).click();
  await expect(page.locator(".appt", { hasText: name }).getByText("Waiting for a review")).toBeVisible();
});

test("a payer and an employer have no services screen", async ({ page }) => {
  const payer = PARTNER_LOGINS[3];
  await signIn(page, "partners", payer.login, payer.org);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.locator(".nav-item", { hasText: "My services" })).toHaveCount(0);
});

test("a clinic administrator is pointed to the staff console and gets no session here", async ({ page }) => {
  await signIn(page, "partners", LOGINS.admin);
  await expect(page.getByText(/belongs in the staff console/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Overview" })).toHaveCount(0);
});

// ---- team, programme counts, plans and cover checks
test("a lab adds a colleague and sees the invitation waiting", async ({ page }) => {
  const lab = PARTNER_LOGINS[0];
  await signIn(page, "partners", lab.login, lab.org);
  await page.locator(".nav-item", { hasText: "My team" }).click();
  const email = `colleague.${Date.now()}@example.org`;
  await page.getByLabel("Colleague's email").fill(email);
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByText(`An invitation was emailed to ${email}`)).toBeVisible();
  await expect(page.locator(".appt", { hasText: email }).getByText("invitation not yet accepted")).toBeVisible();
});

test("an employer enrols people and only ever sees counts", async ({ page }) => {
  const employer = PARTNER_LOGINS[4];
  await signIn(page, "partners", employer.login, employer.org);
  await page.locator(".nav-item", { hasText: "Programme" }).click();
  const stamp = Date.now();
  const refs = [`rb-${stamp}-1`, `rb-${stamp}-2`, `rb-${stamp}-3`];
  await page.getByLabel(/References/).fill(refs.join(", "));
  await page.getByRole("button", { name: /^Enrol/ }).click();
  await expect(page.getByText(/3 enrolled|\d+ enrolled/)).toBeVisible();
  const enrolled = page.locator(".card, .stat", { hasText: "Enrolled" }).first();
  await expect(enrolled).toContainText(/Hidden|\d+/);
  for (const ref of refs) await expect(page.getByText(ref)).toHaveCount(0);   // never shown back
});

test("a payer sets up a plan and a member and gets a clear cover answer", async ({ page }) => {
  const payer = PARTNER_LOGINS[3];
  await signIn(page, "partners", payer.login, payer.org);
  await page.locator(".nav-item", { hasText: "Plans and members" }).click();
  const stamp = String(Date.now()).slice(-8);
  await page.getByLabel("Plan code (capitals)").fill(`P${stamp}`);
  await page.getByLabel("Plan name").fill(`Robot plan ${stamp}`);
  await page.getByLabel(/Kinds of service/).fill("diagnostics, general_physician");
  await page.getByLabel(/The patient pays/).fill("15");
  await page.getByRole("button", { name: "Add plan" }).click();
  await expect(page.getByText(`P${stamp}:`)).toBeVisible();
  await page.locator("form", { has: page.getByRole("button", { name: "Add member" }) }).locator("select").selectOption(`P${stamp}`);
  await page.getByLabel("Member number").last().fill(`RB-${stamp}`);
  await page.getByRole("button", { name: "Add member" }).click();
  await expect(page.getByText(`…${stamp.slice(-4)}`).first()).toBeVisible();   // shown shortened
  await page.getByLabel("Member number").first().fill(`RB-${stamp}`);
  await page.getByLabel("Kind of service").fill("diagnostics");
  await page.getByRole("button", { name: "Check cover" }).click();
  await expect(page.getByTestId("entitlement-answer")).toContainText(`under P${stamp}: the patient pays 15%`);
  await page.getByLabel("Kind of service").fill("dentistry");
  await page.getByRole("button", { name: "Check cover" }).click();
  await expect(page.getByTestId("entitlement-answer")).toContainText("This kind of service is not in the plan");
});

// ---- every home screen shows live numbers; the developer console works
for (const p of PARTNER_LOGINS) {
  test(`${p.name} (${p.role}) sees real numbers on the home screen`, async ({ page }) => {
    await signIn(page, "partners", p.login, p.org);
    const numbers = page.getByLabel("Your numbers");
    await expect(numbers).toBeVisible();
    await expect(numbers.getByText("People on your team")).toBeVisible();
    const text = await numbers.innerText();
    expect(text).toMatch(/\d/);                 // an actual number, never a made-up word
  });
}

test("a partner developer makes a key, sees its secret once, and finds the documents", async ({ page }) => {
  const dev = PARTNER_LOGINS[5];
  await signIn(page, "partners", dev.login, dev.org);
  await expect(page.getByLabel("Your numbers").getByText("API keys active")).toBeVisible();
  await page.locator(".nav-item", { hasText: "API keys" }).click();
  const name = `Robot ERP ${Date.now()}`;
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: "Add key" }).click();
  await expect(page.getByTestId("secret-note")).toContainText(`Secret for ${name}, shown once:`);
  await expect(page.locator(".appt", { hasText: name }).getByText("active")).toBeVisible();
  await page.locator(".nav-item", { hasText: "API documents" }).click();
  await expect(page.getByRole("link", { name: "Open openapi.json" })).toHaveAttribute("href", "/api/v1/openapi.json");
  await expect(page.getByRole("link", { name: "Open asyncapi.json" })).toHaveAttribute("href", "/api/v1/asyncapi.json");
});

test("a partner developer sees the webhooks screen and the delivery log", async ({ page }) => {
  const dev = PARTNER_LOGINS[5];
  await signIn(page, "partners", dev.login, dev.org);
  await page.locator(".nav-item", { hasText: "Webhooks" }).click();
  await expect(page.getByRole("heading", { name: "Webhooks", exact: true })).toBeVisible();
  await expect(page.getByText("Recent deliveries")).toBeVisible();
});
