import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** Where each app runs, and who the robots sign in as. The test clinic comes from scripts/seed_e2e.py. */
export const APPS = {
  staff: process.env.E2E_STAFF ?? "http://localhost:5173",
  patient: process.env.E2E_PATIENT ?? "http://localhost:5174",
  platform: process.env.E2E_PLATFORM ?? "http://localhost:5175",
  doctor: process.env.E2E_DOCTOR ?? "http://localhost:5176",
  partners: process.env.E2E_PARTNERS ?? "http://localhost:5177",
};
// Set E2E_STAFF, E2E_PATIENT, E2E_PLATFORM, E2E_DOCTOR or E2E_PARTNERS if an app is on another address.
// Where the robots reach the API directly. Set E2E_API if the backend is not on port 8000.
export const API = process.env.E2E_API ?? "http://127.0.0.1:8000/api/v1/";
export const ORG = "E2E Clinic";
export const PASSWORD = "E2e-Pass-2026!";
export const LOGINS = {
  admin: "e2e.admin@example.org",
  patient: "e2e.patient@example.org",
  doctor: "e2e.doctor@example.org",
};
export const SERVICE = "E2E General Consult";

/** Sign in to an app's sign-in page the way a person does. */
export async function signIn(page: Page, app: keyof typeof APPS, login: string, org = ORG, password = PASSWORD) {
  await page.goto(`${APPS[app]}/`);
  await page.getByLabel("Organization").fill(org);
  await page.getByLabel(app === "patient" ? "Email" : "Login").fill(login);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Log In", exact: true }).click();
}

/** Sign in through the API (no screen) to set things up or tidy afterwards. Returns the token. */
export async function apiToken(request: APIRequestContext, login: string, org = ORG): Promise<string> {
  const r = await request.post(`${API}auth/login`, { data: { organization: org, login, password: PASSWORD } });
  expect(r.status(), `API sign-in as ${login}`).toBe(200);
  return (await r.json()).token as string;
}

export const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });

/** Give the test clinic two free times on a day far in the future that no earlier run used, so the
 *  robot always finds something to book. Returns how many were made and the day. */
export async function addFreeTimes(request: APIRequestContext, count = 2, withDoctor = false) {
  const token = await apiToken(request, LOGINS.admin);
  const headers = bearer(token);
  const services = (await (await request.get(`${API}services?all=1`, { headers })).json()).services as
    { id: string; name: string }[];
  const service = services.find((s) => s.name === SERVICE);
  expect(service, `the service "${SERVICE}" (run scripts/seed_e2e.py)`).toBeTruthy();
  const doctor = withDoctor
    ? (await (await request.post(`${API}auth/login`, { data: { organization: ORG, login: LOGINS.doctor, password: PASSWORD } })).json()).actor.id as string
    : undefined;
  const place = ((await (await request.get(`${API}locations`, { headers })).json()).locations as { id: string }[])[0];
  for (let attempt = 0; attempt < 10; attempt++) {
    const day = new Date(Date.now() + (30 + Math.floor(Math.random() * 600)) * 86_400_000);
    const first = `${day.toISOString().slice(0, 10)}T09:00:00`;
    const made = await request.post(`${API}services/${service!.id}/slots`, {
      headers, data: { location: place.id, first_local: first, count, ...(doctor ? { provider: doctor } : {}) },
    });
    if (made.status() === 201) return { serviceId: service!.id, first, token };
  }
  throw new Error("could not find a free day for new times");
}

/** Cancel every active appointment the test patient has, so each robot starts clean and leaves no mess. */
export async function cancelPatientsBookings(request: APIRequestContext) {
  const token = await apiToken(request, LOGINS.patient);
  const headers = bearer(token);
  const rows = (await (await request.get(`${API}bookings`, { headers })).json()).bookings as
    { id: string; status: string }[];
  for (const b of rows.filter((x) => x.status !== "cancelled")) {
    await request.post(`${API}bookings/${b.id}/cancel`, { headers, data: {} });
  }
  // A patient's cancel of a CONFIRMED appointment only asks; the clinic answers it (GHE-14 T-26).
  const staff = bearer(await apiToken(request, LOGINS.admin));
  const left = (await (await request.get(`${API}bookings`, { headers: staff })).json()).bookings as
    { id: string; status: string }[];
  for (const b of left.filter((x) => x.status !== "cancelled")) {
    await request.post(`${API}bookings/${b.id}/cancel`, { headers: staff, data: {} });
  }
}

/** The patient books the first free time through the API (setup for robots that start after booking). */
export async function patientBooksViaApi(request: APIRequestContext, serviceId: string, withDoctor = false) {
  const token = await apiToken(request, LOGINS.patient);
  const headers = bearer(token);
  const slots = (await (await request.get(`${API}services/${serviceId}/slots`, { headers })).json()).slots as
    { id: string; provider?: { name: string } | null }[];
  const pick = withDoctor ? slots.filter((x) => x.provider) : slots;
  expect(pick.length, "free times to book").toBeGreaterThan(0);
  const r = await request.post(`${API}bookings`, {
    headers: { ...headers, "Idempotency-Key": crypto.randomUUID().replace(/-/g, "") },
    data: { slot: pick[0].id },
  });
  expect(r.status()).toBe(201);
  return (await r.json()).booking.id as string;
}

/** Clinic staff confirm a booking through the API (setup). */
export async function staffConfirms(request: APIRequestContext, bookingId: string) {
  const token = await apiToken(request, LOGINS.admin);
  const r = await request.post(`${API}bookings/${bookingId}/confirm`, { headers: bearer(token), data: {} });
  expect(r.status()).toBe(200);
}

export const PLATFORM = { org: "Zoiko Platform", password: "Demo-Pass-2026!",
  asker: "vikram@zoiko.example", approver: "priya@zoiko.example" };

/** Does the test clinic's admin get in right now? (used to see a suspension take effect) */
export async function clinicAdminCanSignIn(request: APIRequestContext): Promise<boolean> {
  const r = await request.post(`${API}auth/login`, { data: { organization: ORG, login: LOGINS.admin, password: PASSWORD } });
  return r.status() === 200;
}

/** The test doctor chooses a display name (setup), the way the doctor app does it. */
export async function doctorSetsName(request: APIRequestContext, name: string) {
  const token = await apiToken(request, LOGINS.doctor);
  const r = await request.post(`${API}auth/display-name`, { headers: bearer(token), data: { name } });
  expect(r.status()).toBe(200);
}

/** Remove any name check on the test doctor (409 means there was none), so a robot starts clean. */
export async function clearNameCheck(request: APIRequestContext) {
  const doctor = (await (await request.post(`${API}auth/login`, {
    data: { organization: ORG, login: LOGINS.doctor, password: PASSWORD } })).json()).actor.id as string;
  const admin = await apiToken(request, LOGINS.admin);
  await request.post(`${API}staff/providers/${doctor}/unverify`, {
    headers: bearer(admin), data: { reason: "robot cleanup" } });
}

/** The test patient's insurance, through the API: add one, choose sharing, and remove them again. */
export async function patientInsurance(request: APIRequestContext) {
  const token = await apiToken(request, LOGINS.patient);
  const headers = bearer(token);
  return {
    async add(insurer: string) {
      const r = await request.post(`${API}coverage`, { headers, data: { insurer, plan: "Robot", member_id: "ROBOT-0000-1234" } });
      expect(r.status()).toBe(201);
    },
    async share(on: boolean) {
      const r = await request.post(`${API}coverage/sharing`, { headers, data: { share: on } });
      expect(r.status()).toBe(200);
    },
    async clear() {
      await request.post(`${API}coverage/sharing`, { headers, data: { share: false } });
      const rows = (await (await request.get(`${API}coverage`, { headers })).json()).coverage as { id: string }[];
      for (const c of rows) await request.post(`${API}coverage/${c.id}/end`, { headers, data: {} });
    },
  };
}

export const SAFETY = { first: "iyer@zoiko.example", second: "rao@zoiko.example" };

/** The partner, payer and employer test customers from scripts/seed_partners.py (one login each). */
export const PARTNER_LOGINS = [
  { role: "R05", org: "E2E Lab", login: "e2e.lab@example.org", name: "Lab or imaging operator", services: true },
  { role: "R06", org: "E2E Pharmacy", login: "e2e.pharmacy@example.org", name: "Pharmacy or dispensing partner", services: true },
  { role: "R09", org: "E2E Homecare", login: "e2e.homecare@example.org", name: "Community or home-care partner", services: true },
  { role: "R07", org: "E2E Payer", login: "e2e.payer@example.org", name: "Payer, insurer or TPA", services: false },
  { role: "R08", org: "E2E Employer", login: "e2e.employer@example.org", name: "Employer sponsor", services: false },
  { role: "R10", org: "E2E Developer", login: "e2e.developer@example.org", name: "Integration partner developer", services: false },
];
