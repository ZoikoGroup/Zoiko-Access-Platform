// The only file that talks to the backend. Pages call these functions, never fetch().
import type { BookingHistoryData } from "@shared/BookingHistory";
import { friendlyEmailError } from "@shared/EmailCard";
import { friendlyNameError } from "@shared/NameCard";
import { friendlyPasswordError } from "@shared/ChangePassword";
import type {
  BookingSystem, DeveloperClient, DeveloperData, ExceptionsData, JourneyHistory, JourneysData, OperationHistory,
} from "./consoleTypes";
import type {
  ClinicSettings, CoverageRow, WorkItem, FamilyRequest, FreeTime, IncomingReferral, ReferralInbox, MessageThread, Profile, ProviderRow, Reports, ThreadView, WaitingRow,
  BookingRow, Compliance, Dashboard, Directory, Health, LocationRow, Me, PatientRow, ServiceRow,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly code?: string) {
    super(message);
  }
}

async function ping(path: string): Promise<Health> {
  try {
    const res = await fetch(path, { cache: "no-store" });
    return res.ok ? "ok" : "down";
  } catch {
    return "down";
  }
}

export async function checkHealth(): Promise<{ api: Health; database: Health }> {
  const [api, database] = await Promise.all([ping("/healthz"), ping("/readyz")]);
  return { api, database };
}

// ---------------------------------------------------------------------------------------
// Session and staff actions use the real /api/v1 endpoints with a signed token from sign-in.
// ---------------------------------------------------------------------------------------
export interface Session {
  token: string;            // signed session token (or an unsigned dev token in development)
  tenantId: string;
  tenantName: string;
  login: string;
  roles: { role: string; organization: { id: string; name: string } }[];
}

/** What the clinic-operations screens need: the organization this person manages. */
export interface Staff {
  organization: string;
  organizationName: string;
}

const SESSION_KEY = "zoiko.ops.session.v2";
let current: Session | null = null;

export function loadSession(): Session | null {
  // DEV ONLY shortcut: /?tenant=<id>&as=<login id> uses the unsigned dev token (backend dev flags).
  const q = new URLSearchParams(window.location.search);
  const tenant = q.get("tenant");
  const subject = q.get("as");
  if (import.meta.env.DEV && tenant && subject) {
    setSession({
      token: `dev:${tenant}:${q.get("issuer") ?? "dev-idp"}:${subject}`, tenantId: tenant, tenantName: "(dev link)",
      login: subject, roles: [{ role: "R04", organization: { id: q.get("org") ?? "", name: "(dev link)" } }],
    });
    return current;
  }
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    current = raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    current = null;
  }
  return current;
}

export function setSession(session: Session | null): void {
  current = session;
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage blocked: the session lives in memory only */
  }
}

export function staffFromSession(session: Session | null): Staff | null {
  const r = session?.roles.find((x) => x.role === "R04" || x.role === "R13");
  return r ? { organization: r.organization.id, organizationName: r.organization.name } : null;
}

export interface LoginReply {
  token: string;
  home: string;
  tenant: { id: string; name: string };
  actor: { login: string };
  roles: Session["roles"];
}

/** Every failure is the same message on purpose: the server does not say which part was wrong. */
export async function signIn(organization: string, login: string, password: string): Promise<LoginReply> {
  const res = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization, login, password }),
  });
  if (!(res.headers.get("Content-Type") ?? "").includes("json")) {
    throw new ApiError("Cannot reach the server");
  }
  const body = (await res.json()) as LoginReply & { title?: string; code?: string };
  if (res.status === 401) throw new ApiError("Wrong organization, login or password.", "UNAUTHENTICATED");
  if (!res.ok) throw new ApiError(body.title ?? `Sign-in failed (${res.status})`, body.code);
  return body;
}

/** Sign out everywhere: the server revokes every token this person was issued. */
export async function signOutEverywhere(): Promise<void> {
  try {
    if (current) {
      await fetch("/api/v1/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${current.token}` } });
    }
  } catch {
    /* we still forget the token below */
  }
  setSession(null);
}

async function v1<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!current) throw new ApiError("Not signed in", "UNAUTHENTICATED");
  const res = await fetch(`/api/v1/${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${current.token}`,
      ...(init.headers ?? {}),
    },
  });
  const isJson = (res.headers.get("Content-Type") ?? "").includes("json");
  if (!isJson) throw new ApiError("The server sent something that is not JSON (is the backend reachable?)");
  const body = (await res.json()) as T & { title?: string; code?: string };
  if (!res.ok) throw new ApiError(body.title ?? `Request failed (${res.status})`, body.code);
  return body;
}

const post = <T,>(path: string, body: unknown = {}) =>
  v1<T>(path, { method: "POST", body: JSON.stringify(body) });

export const listBookings = async () => (await v1<{ bookings: BookingRow[] }>("bookings")).bookings;
export const confirmBooking = (id: string) => post(`bookings/${encodeURIComponent(id)}/confirm`);
export const cancelBooking = (id: string, reason = "") =>
  post(`bookings/${encodeURIComponent(id)}/cancel`, reason ? { reason } : {});
export const markAttendance = (id: string, outcome: "attended" | "no_show") =>
  post(`bookings/${encodeURIComponent(id)}/attendance`, { outcome });
export const listWaitlist = async () => (await v1<{ entries: WaitingRow[] }>("staff/waitlist")).entries;
export const removeFromWaitlist = (id: string) => post(`waitlist/${encodeURIComponent(id)}/leave`);
export const getProfile = async (person: string) =>
  (await v1<{ profile: Profile }>(`people/${encodeURIComponent(person)}/profile`)).profile;
export const saveProfile = (person: string, body: { name: string; birth_date: string; phone: string }) =>
  post(`people/${encodeURIComponent(person)}/profile`, body);
export const listPatientCoverage = async (person: string) =>
  v1<{ coverage: CoverageRow[]; shared: boolean }>(`people/${encodeURIComponent(person)}/coverage`);
export const addPatientCoverage = (person: string, body: {
  insurer: string; plan: string; member_id: string; valid_until: string;
}) => post("coverage", { person, ...body });
export const endPatientCoverage = (id: string) => post(`coverage/${encodeURIComponent(id)}/end`);
export const listFamilyRequests = async () =>
  await v1<{ requests: FamilyRequest[]; approved: FamilyRequest[] }>("staff/family-requests");
export const familyStep = (id: string, step: "proof-needed" | "proof-received" | "suspend" | "resume", reason = "") =>
  post(`staff/family-requests/${encodeURIComponent(id)}/${step}`, reason ? { reason } : {});
export const approveFamily = (id: string, validUntil: string, scopes: string[]) =>
  post(`staff/family-requests/${encodeURIComponent(id)}/approve`, { verified: true, valid_until: validUntil, scopes });
export const rejectFamily = (id: string) => post(`staff/family-requests/${encodeURIComponent(id)}/reject`);
export const listMessageThreads = async () => (await v1<{ threads: MessageThread[] }>("messages/threads")).threads;
export const getMessageThread = async (id: string) =>
  (await v1<{ thread: ThreadView }>(`messages/threads/${encodeURIComponent(id)}`)).thread;
export const replyMessageThread = (id: string, body: string) =>
  post(`messages/threads/${encodeURIComponent(id)}/reply`, { body });
export const closeMessageThread = (id: string) => post(`messages/threads/${encodeURIComponent(id)}/close`);
export const CLOSED_DAYS = 14;                       // how far back "recently closed" looks
export const listWork = (filter: "" | "mine" | "unassigned" | "closed") =>
  v1<{ me: string; items: WorkItem[] }>(`staff/work${
    filter === "closed" ? `?closed=${CLOSED_DAYS}` : filter ? `?${filter === "mine" ? "mine=1" : "unassigned=1"}` : ""}`);
export interface AiPermit {
  id: string; feature_id: string; purpose: string; lane: string; level: string; state: string;
  allowed_tools: string[]; expiry: string | null; signers: string[]; created_by: string;
  steps: { from: string; to: string; by: string; reason: string; at: string }[];
}
export const getAiPermits = () => v1<{ me: string; permits: AiPermit[] }>("staff/ai/permits");
export const proposeAiPermit = (b: {
  feature_id: string; purpose: string; lane: string; level: string; model: string; prompt: string;
  tools: string; clinical_owner: string;
}) => post("staff/ai/permits", b);
export const signAiPermit = (id: string) => post(`staff/ai/permits/${encodeURIComponent(id)}/sign`);
export const advanceAiPermit = (id: string, to: string, reason = "", days?: number) =>
  post(`staff/ai/permits/${encodeURIComponent(id)}/advance`, { to, reason, ...(days ? { days } : {}) });
export const claimWork = (id: string) => post(`staff/work/${encodeURIComponent(id)}/claim`);
export const releaseWork = (id: string) => post(`staff/work/${encodeURIComponent(id)}/release`);
/** Every step of one work item, oldest first (who took it, gave it back, closed it). */
export const getWorkHistory = (id: string) =>
  v1<BookingHistoryData>(`staff/work/${encodeURIComponent(id)}/history`);
/** Change the signed-in person's password. The server ends every other session and returns a fresh
 *  token for this one, which replaces the stored token so this tab stays signed in. */
export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  if (current?.token.startsWith("dev:")) {
    throw new Error("A development link has no password. Sign in with your login and password to change it.");
  }
  try {
    const body = await v1<{ token: string }>("auth/change-password", {
      method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (current) setSession({ ...current, token: body.token });
  } catch (e) {
    throw friendlyPasswordError(e);
  }
}

export const getSettings = () => v1<ClinicSettings>("staff/settings");
export const setResponseTarget = (hours: number | null) => post("staff/settings", { hours });
export const getProviders = async () =>
  (await v1<{ days: number; can_manage: boolean; bases: Record<string, string>; providers: ProviderRow[] }>("staff/providers"));
export const verifyProviderName = (id: string, basis: string, note: string) =>
  post(`staff/providers/${encodeURIComponent(id)}/verify`, { basis, note });
export const withdrawProviderCheck = (id: string, reason: string) =>
  post(`staff/providers/${encodeURIComponent(id)}/unverify`, { reason });
export const getReports = (days: number) => v1<Reports>(`staff/reports?days=${days}`);
/** Every step of one appointment, worded for clinic staff (never a patient name). */
export const getBookingHistory = (id: string) =>
  v1<BookingHistoryData>(`bookings/${encodeURIComponent(id)}/history`);

export const listFreeTimes = async (serviceId: string) =>
  (await v1<{ slots: FreeTime[] }>(`services/${encodeURIComponent(serviceId)}/slots`)).slots;
export const rescheduleBooking = (id: string, slot: string) =>
  post(`bookings/${encodeURIComponent(id)}/reschedule`, { slot });
export const listStaffServices = async () => (await v1<{ services: ServiceRow[] }>("services?all=1")).services;
export const listLocations = async () => (await v1<{ locations: LocationRow[] }>("locations")).locations;

export const createService = (body: {
  organization: string; name: string; category: string; duration_minutes: number;
  mode: string; description: string;
}) => post("services", body);

export const changeListing = (id: string, action: string, note = "") =>
  post(`services/${encodeURIComponent(id)}/listing`, { action, note });
export const setServiceStatus = (id: string, status: "active" | "inactive") =>
  post(`services/${encodeURIComponent(id)}/status`, { status });

export const createLocation = (body: {
  organization: string; name: string; time_zone: string; address: string; phone: string;
}) => post("locations", body);

export const updateLocationContact = (id: string, phone: string, address: string) =>
  post(`locations/${encodeURIComponent(id)}/contact`, { phone, address });

export const addSlots = (serviceId: string, body: {
  location: string; first_local: string; count: number; provider?: string;
  days?: number; weekdays_only?: boolean;
}) => post<{ created: number }>(`services/${encodeURIComponent(serviceId)}/slots`, body);

/** Take free future times off the calendar for a range of days (both ends included). With
 *  `dry_run` it only counts. Times with a booking are never touched. */
export const withdrawSlots = (serviceId: string, body: {
  first_day: string; last_day: string; provider?: string; dry_run?: boolean;
}) => post<{ count: number; dry_run: boolean }>(`services/${encodeURIComponent(serviceId)}/slots/withdraw`, body);

/** Take ONE free future time off the calendar. Refused if it has a booking. */
export const withdrawSlot = (slotId: string) =>
  post<{ withdrawn: boolean }>(`slots/${encodeURIComponent(slotId)}/withdraw`, {});

export const listIncomingReferrals = async () => (await v1<{ referrals: IncomingReferral[] }>("referrals/incoming")).referrals;
export const acknowledgeReferral = (id: string) => post(`referrals/${encodeURIComponent(id)}/acknowledge`);
export const getReferralInbox = () => v1<ReferralInbox>("staff/referral-inbox");
export const setReferralInbox = (owner: string, fallback: string) =>
  post<ReferralInbox>("staff/referral-inbox", { owner, fallback });
export const getMe = () => v1<Me>("me");
export const getDashboard = () => v1<Dashboard>("staff/dashboard");
export const getCompliance = () => v1<Compliance>("staff/compliance");
export const getDirectory = () => v1<Directory>("staff/directory");
export const getPatients = async () => (await v1<{ patients: PatientRow[] }>("staff/patients")).patients;

// ---- team management (administrators only; the server enforces it) ----
export const inviteStaff = (email: string, role: string) => post("staff/members", { email, role });
export const setMemberStatus = (id: string, status: "active" | "suspended") =>
  post(`staff/members/${encodeURIComponent(id)}/status`, { status });
export const resendInvite = (id: string) => post(`staff/members/${encodeURIComponent(id)}/resend-invite`);

// ---- links in emails (no sign-in needed): choosing a password from an invitation ----
export async function verifyEmail(token: string): Promise<boolean> {
  try {
    const res = await fetch("/api/v1/auth/verify-email", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function resetPassword(token: string, password: string): Promise<void> {
  const res = await fetch("/api/v1/auth/reset-password", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token, password }),
  });
  if (res.ok) return;
  const body = (await res.json().catch(() => ({}))) as { fields?: string[] };
  if (res.status === 422 && body.fields?.[0] === "password") {
    throw new ApiError("That password is too short or too easy to guess. Use a longer one that is not your email.", "VALIDATION_FAILED");
  }
  throw new ApiError("This link has expired or was already used. Ask your administrator to send a new one.", "BAD_LINK");
}

export const getEmailStatus = async () => (await v1<{ email: { email: string; verified: boolean } }>("me")).email;

/** Set or change this person's email address (the current password is required). */
export async function setEmail(email: string, currentPassword: string) {
  try {
    return await v1<{ email: string; verified: boolean; sent: boolean }>("auth/email", {
      method: "POST", body: JSON.stringify({ email, current_password: currentPassword }),
    });
  } catch (e) {
    throw friendlyEmailError(e);
  }
}

export async function resendVerification(): Promise<"sent" | "wait"> {
  try {
    await v1("auth/resend-verification", { method: "POST", body: "{}" });
    return "sent";
  } catch (e) {
    if ((e as { code?: string }).code === "RATE_LIMITED") return "wait";
    throw e;
  }
}

export const getDisplayName = async () => (await v1<{ display_name: string }>("me")).display_name;

/** Choose the name this person is shown by (typed by them, not verified). */
export async function saveDisplayName(name: string): Promise<string> {
  try {
    return (await v1<{ name: string }>("auth/display-name", {
      method: "POST", body: JSON.stringify({ name }),
    })).name;
  } catch (e) {
    throw friendlyNameError(e);
  }
}

export async function clearDisplayName(): Promise<void> {
  await v1("auth/display-name", { method: "DELETE" });
}

// ---------------------------------------------------------------------------------------
// Partner connections, journeys and integrations (backend/api/console.py).
// ---------------------------------------------------------------------------------------
const enc = encodeURIComponent;

export const getExceptions = () => v1<ExceptionsData>("staff/exceptions");
export const getConnectorHistory = async (id: string) =>
  (await v1<{ history: OperationHistory }>(`staff/connector/${enc(id)}/history`)).history;
export const redriveOperation = (id: string) => post(`staff/connector/${enc(id)}/redrive`);
export const resolveOperation = (id: string, outcome: "confirmed" | "rejected") =>
  post(`staff/connector/${enc(id)}/resolve`, { outcome });
export const resolveJourneyException = (id: string) =>
  post(`staff/workflow-exceptions/${enc(id)}/resolve`);

export const getJourneys = () => v1<JourneysData>("staff/journeys");
export const getJourneyHistory = async (id: string) =>
  (await v1<{ history: JourneyHistory }>(`staff/journeys/${enc(id)}/history`)).history;

export const getBookingSystem = () => v1<BookingSystem>("staff/booking-system");
export const setBookingSystem = (system: string) => post<BookingSystem>("staff/booking-system", { system });

export const getDeveloper = () => v1<DeveloperData>("staff/developer");
export const createApiClient = (body: { name: string; environment: string; scopes: string[]; rate_limit?: number }) =>
  post<{ client: DeveloperClient; key: string }>("staff/developer/clients", body);
export const revokeApiClient = (id: string) => post(`staff/developer/clients/${enc(id)}/revoke`);
export const testWebhook = (client: string, hook: string) =>
  post(`staff/developer/clients/${enc(client)}/webhooks/${enc(hook)}/test`);
export const redriveDelivery = (client: string, delivery: string) =>
  post(`staff/developer/clients/${enc(client)}/deliveries/${enc(delivery)}/redrive`);
