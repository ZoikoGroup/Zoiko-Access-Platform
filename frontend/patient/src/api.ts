import type { BookingHistoryData } from "@shared/BookingHistory";
import { friendlyEmailError } from "@shared/EmailCard";
import { friendlyPasswordError } from "@shared/ChangePassword";
// The only file that talks to the backend. Pages call these functions, never fetch().

export interface Session {
  token: string;        // a signed session token from /auth/login (or a dev token in development)
  tenantName: string;
  login: string;
}

export interface Me {
  email: { email: string; verified: boolean };
  actor: string;
  type: string;
  tenant: string;
  cell: string;
  purpose: string;
  persons: string[];
  roles: { role: string; organization: { id: string; name: string } }[];
}

export interface Service {
  id: string;
  name: string;
  category: string;
  description: string;
  duration_minutes: number;
  mode: string;
  organization: { id: string; name: string };
  next_slot: string | null;
  next_slot_local: string | null;
  next_slot_zone: string | null;
  usual_confirmation_hours?: number | null;
}

export interface Slot {
  id: string;
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_zone: string;
  location: string;
  provider: { id: string; name: string; verified?: boolean } | null;
}

export interface Booking {
  id: string;
  status: "requested" | "confirmed" | "cancelled";
  created_at: string;
  person: string;
  service: { id: string; name: string; mode: string };
  organization: { id: string; name: string };
  location: { id: string; name: string; address: string; phone?: string };
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_zone: string;
  provider: { id: string; name: string; verified?: boolean } | null;
  rescheduled_from: string | null;     // the appointment this one replaced
  attendance: "attended" | "no_show" | null;   // recorded by the clinic after the visit time
  // A change the patient asked for that the clinic has not answered yet: the appointment stays.
  pending_change: "cancel" | "rebook" | null;
}

export interface Referral {
  id: string;
  state: "issued" | "accepted" | "needs_info" | "declined";
  urgency: "routine" | "urgent";
  reason: string;
  decision_reason: string;
  created_at: string;
  referrer: { id: string; name: string };
  service: { id: string; name: string };
  organization: { id: string; name: string };
}

export interface ProfileRow {
  person: string;
  own: boolean;                      // this is the login's own record, not a family member's
  editable: boolean;
  name: string;
  birth_date: string;
  phone: string;
  /** Whether a clinic has checked who this patient is. */
  identity?: {
    status: "verified" | "unverified"; checked_on: string | null; valid_until: string | null;
    clinic: string; why_not: "" | "withdrawn" | "check_failed" | "expired" | "details_changed";
  };
}

export interface CoverageRow {
  id: string;
  person: string;
  insurer: string;
  plan: string;
  member_last4: string;               // the member number is never sent in full
  valid_from: string | null;
  valid_until: string | null;
  status: "active" | "ended";
  in_force: boolean;
}

export interface CareTeamPerson {
  person: string;
  team: { clinician: string; basis: string; since: string; until: string | null }[];
}

export interface AccessEntry {
  at: string;
  who: string;
  role: string | null;
  operation: string;
  purpose: string;
  allowed: boolean;
  person: string;
}

export interface FamilyRow {
  id: string;
  // proposed, proof_required and approval_pending all mean "waiting for the clinic"; suspended is paused
  status: "proposed" | "proof_required" | "approval_pending" | "active" | "suspended";
  relationship: string;
  person: string;
  name: string;
  birth_date: string;
  valid_until: string | null;
  scopes?: string[];                    // what the clinic allowed this person to do
}

export interface MessageThread {
  id: string;
  subject: string;
  status: "open" | "closed";
  last_message_at: string;
  unread: number;
  unread_for_me?: number;               // what THIS login has not opened
}

export interface ThreadView extends MessageThread {
  messages: { id: string; side: "patient" | "staff"; body: string; at: string; mine: boolean; seen_at?: string | null }[];
}

export interface WaitEntry {
  id: string;
  state: "waiting" | "offered";            // offered = a time opened up for you
  joined_at: string;
  offered_at: string | null;
  service: { id: string; name: string };
  organization: { id: string; name: string };
}

export interface Notice {
  id: string;
  person: string;
  kind: string;
  title: string;
  body: string;
  booking: string;
  created_at: string;
  read: boolean;
}

export class ApiError extends Error {
  constructor(message: string, readonly code: string, readonly status: number) {
    super(message);
  }
}

// ---- session: kept for this browser tab only ----
const SESSION_KEY = "zoiko.patient.session.v2";
let current: Session | null = null;

export function loadSession(): Session | null {
  // DEV ONLY shortcut: /?tenant=<id>&as=<login id> uses the unsigned dev token (backend dev flags).
  const q = new URLSearchParams(window.location.search);
  const tenant = q.get("tenant");
  const subject = q.get("as");
  if (import.meta.env.DEV && tenant && subject) {
    setSession({ token: `dev:${tenant}:${q.get("issuer") ?? "dev-idp"}:${subject}`, tenantName: "(dev link)", login: subject });
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

function authHeader(): Record<string, string> {
  if (!current) throw new ApiError("Not signed in", "UNAUTHENTICATED", 401);
  return { Authorization: `Bearer ${current.token}` };
}

export interface LoginReply {
  token: string;
  home: string;
  tenant: { id: string; name: string };
  actor: { login: string };
}

/** Sign in. Every failure is the same message on purpose: the server does not say which part was wrong. */
export async function signIn(organization: string, login: string, password: string): Promise<LoginReply> {
  const res = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization, login, password }),
  });
  if (!(res.headers.get("Content-Type") ?? "").includes("json")) {
    throw new ApiError("Cannot reach the server", "BAD_RESPONSE", res.status);
  }
  const body = (await res.json()) as LoginReply & { title?: string; code?: string };
  if (res.status === 401) throw new ApiError("Wrong organization, login or password.", "UNAUTHENTICATED", 401);
  if (!res.ok) throw new ApiError(body.title ?? `Sign-in failed (${res.status})`, body.code ?? "ERROR", res.status);
  return body;
}

/** Create a patient account (only works where the organization has turned self-registration on). */
export async function registerAccount(organization: string, login: string, password: string): Promise<LoginReply> {
  const res = await fetch("/api/v1/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization, login, password }),
  });
  if (!(res.headers.get("Content-Type") ?? "").includes("json")) {
    throw new ApiError("Cannot reach the server", "BAD_RESPONSE", res.status);
  }
  const body = (await res.json()) as LoginReply & { title?: string; code?: string; fields?: string[] };
  if (res.ok) return body;
  const messages: Record<number, string> = {
    403: "This organization does not accept new sign-ups. Ask them for an invitation.",
    409: "That email is already registered. Try signing in instead.",
    429: "Too many sign-ups right now. Please try again in a few minutes.",
  };
  if (res.status === 422) {
    const field = body.fields?.[0];
    throw new ApiError(field === "password"
      ? "That password is too short or too easy to guess. Use a longer one that is not your email."
      : field === "login" ? "Enter a valid email address." : "Please check what you entered.", "VALIDATION_FAILED", 422);
  }
  throw new ApiError(messages[res.status] ?? body.title ?? `Sign-up failed (${res.status})`, body.code ?? "ERROR", res.status);
}

/** Sign out everywhere: the server revokes every token this person was issued. */
export async function signOutEverywhere(): Promise<void> {
  try {
    await fetch("/api/v1/auth/logout", { method: "POST", headers: { ...authHeader() } });
  } catch {
    /* even if the server cannot be reached, we still forget the token below */
  }
  setSession(null);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<{ body: T; replayed: boolean }> {
  const res = await fetch(`/api/v1/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeader(), ...(init.headers ?? {}) },
  });
  if (!(res.headers.get("Content-Type") ?? "").includes("json")) {
    throw new ApiError("The server sent something that is not JSON (is the backend reachable?)", "BAD_RESPONSE", res.status);
  }
  const body = (await res.json()) as T & { title?: string; code?: string };
  if (!res.ok) {
    throw new ApiError(body.title ?? `Request failed (${res.status})`, body.code ?? "ERROR", res.status);
  }
  return { body, replayed: res.headers.get("Idempotent-Replayed") === "true" };
}

/** Download one appointment as a calendar file (.ics). The browser cannot send the login with a
 *  plain link, so the file is fetched here and saved from memory. */
export async function downloadCalendar(id: string): Promise<void> {
  const res = await fetch(`/api/v1/bookings/${id}/calendar`, { headers: { ...authHeader() } });
  if (!res.ok) throw new ApiError("Could not make the calendar file", "ERROR", res.status);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "appointment.ics";
  link.click();
  URL.revokeObjectURL(url);
}

/** Save everything the system holds about this person as one JSON file (see my/export). */
export async function downloadMyData(): Promise<void> {
  const res = await fetch("/api/v1/my/export", { headers: { ...authHeader() } });
  if (!res.ok) throw new ApiError("Could not prepare your data", "ERROR", res.status);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "my-zoiko-data.json";
  link.click();
  URL.revokeObjectURL(url);
}

/** Change the signed-in person's password. The server ends every other session and returns a fresh
 *  token for this one, which replaces the stored token so this tab stays signed in. */
export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  if (current?.token.startsWith("dev:")) {
    throw new Error("A development link has no password. Sign in with your login and password to change it.");
  }
  try {
    const { body } = await request<{ token: string }>("auth/change-password", {
      method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (current) setSession({ ...current, token: body.token });
  } catch (e) {
    throw friendlyPasswordError(e);
  }
}

export const getMe = async () => (await request<Me>("me")).body;

export interface ServiceFilters {
  q?: string;
  category?: string;
  mode?: "" | "in_person" | "video";
  available?: boolean;          // only services that have a free time
  sort?: "name" | "soonest";
}

export interface CategoryOption { category: string; count: number }

/** Search the directory. Also returns the categories that have services, for the filter menu. */
export async function searchServices(f: ServiceFilters = {}): Promise<{ services: Service[]; categories: CategoryOption[] }> {
  const p = new URLSearchParams();
  if (f.q?.trim()) p.set("q", f.q.trim());
  if (f.category) p.set("category", f.category);
  if (f.mode) p.set("mode", f.mode);
  if (f.available) p.set("available", "1");
  if (f.sort && f.sort !== "name") p.set("sort", f.sort);
  const query = p.toString();
  return (await request<{ services: Service[]; categories: CategoryOption[] }>(`services${query ? `?${query}` : ""}`)).body;
}

/** Every step of one appointment, worded for the patient (no staff names). */
export const getBookingHistory = async (id: string) =>
  (await request<BookingHistoryData>(`bookings/${encodeURIComponent(id)}/history`)).body;

export const getServices = async (q = "") => (await searchServices({ q })).services;

export const getSlots = async (serviceId: string) =>
  (await request<{ slots: Slot[] }>(`services/${encodeURIComponent(serviceId)}/slots`)).body.slots;

export const getReferrals = async () => (await request<{ referrals: Referral[] }>("referrals")).body.referrals;

export const getBookings = async () => (await request<{ bookings: Booking[] }>("bookings")).body.bookings;

export async function createBooking(slot: string, person?: string): Promise<Booking> {
  // One key per click: retrying the SAME click can never book twice.
  const key = crypto.randomUUID().replace(/-/g, "");
  const { body } = await request<{ booking: Booking }>("bookings", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ slot, ...(person ? { person } : {}) }),
  });
  return body.booking;
}

export async function cancelBooking(id: string, reason = ""): Promise<Booking> {
  const { body } = await request<{ booking: Booking }>(`bookings/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: JSON.stringify(reason ? { reason } : {}),
  });
  return body.booking;
}

export async function rescheduleBooking(id: string, slot: string): Promise<Booking> {
  const { body } = await request<{ booking: Booking }>(`bookings/${encodeURIComponent(id)}/reschedule`, {
    method: "POST",
    body: JSON.stringify({ slot }),
  });
  return body.booking;
}

export const getProfiles = async () => (await request<{ profiles: ProfileRow[] }>("profile")).body.profiles;

export async function saveProfile(person: string, body: { name: string; birth_date: string; phone: string }) {
  await request(`people/${encodeURIComponent(person)}/profile`, { method: "POST", body: JSON.stringify(body) });
}

export const getCoverage = async () => (await request<{ coverage: CoverageRow[] }>("coverage")).body.coverage;

export interface SharingChoice { person: string; share_with_staff: boolean }
export const getSharing = async () =>
  (await request<{ sharing: SharingChoice[] }>("coverage/sharing")).body.sharing;
export async function setSharing(person: string, share: boolean): Promise<void> {
  await request("coverage/sharing", { method: "POST", body: JSON.stringify({ person, share }) });
}

export async function addCoverage(body: {
  insurer: string; plan: string; member_id: string; valid_from: string; valid_until: string;
}): Promise<void> {
  await request("coverage", { method: "POST", body: JSON.stringify(body) });
}

export async function endCoverage(id: string): Promise<void> {
  await request(`coverage/${encodeURIComponent(id)}/end`, { method: "POST", body: "{}" });
}

export const getCareTeam = async () => (await request<{ people: CareTeamPerson[] }>("care-team")).body.people;
export const getAccessLog = async () => (await request<{ entries: AccessEntry[] }>("access-log")).body.entries;

export const getFamily = async () => (await request<{ family: FamilyRow[] }>("family")).body.family;

export async function requestFamily(body: { relationship: string; name: string; birth_date: string }) {
  await request("family", { method: "POST", body: JSON.stringify(body) });
}

export async function withdrawFamily(id: string): Promise<void> {
  await request(`family/${encodeURIComponent(id)}/withdraw`, { method: "POST", body: "{}" });
}

export const getThreads = async () => (await request<{ threads: MessageThread[] }>("messages/threads")).body.threads;
export const getThread = async (id: string) =>
  (await request<{ thread: ThreadView }>(`messages/threads/${encodeURIComponent(id)}`)).body.thread;

export async function startThread(body: { subject: string; body: string }): Promise<string> {
  const { body: made } = await request<{ thread: { id: string } }>("messages/threads", {
    method: "POST", body: JSON.stringify(body),
  });
  return made.thread.id;
}

export async function replyToThread(id: string, body: string): Promise<void> {
  await request(`messages/threads/${encodeURIComponent(id)}/reply`, { method: "POST", body: JSON.stringify({ body }) });
}

export const getWaitlist = async () => (await request<{ entries: WaitEntry[] }>("waitlist")).body.entries;

export async function joinWaitlist(service: string, person?: string): Promise<WaitEntry> {
  const { body } = await request<{ entry: WaitEntry }>("waitlist", {
    method: "POST", body: JSON.stringify({ service, ...(person ? { person } : {}) }),
  });
  return body.entry;
}

export async function leaveWaitlist(id: string): Promise<void> {
  await request(`waitlist/${encodeURIComponent(id)}/leave`, { method: "POST", body: "{}" });
}

export async function getNotifications(): Promise<{ notifications: Notice[]; unread: number }> {
  return (await request<{ notifications: Notice[]; unread: number }>("notifications")).body;
}

export async function markNotificationRead(id: string): Promise<void> {
  await request(`notifications/${encodeURIComponent(id)}/read`, { method: "POST", body: "{}" });
}

// ---- email links and password reset (no sign-in needed) ----
async function publicPost(path: string, body: unknown): Promise<Response> {
  return fetch(`/api/v1/auth/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** True if the link worked. A link works once, so callers must not call this twice. */
export async function verifyEmail(token: string): Promise<boolean> {
  try {
    return (await publicPost("verify-email", { token })).ok;
  } catch {
    return false;
  }
}

/** Always looks the same to the person, whether or not an account exists. */
export async function forgotPassword(organization: string, login: string): Promise<void> {
  const res = await publicPost("forgot-password", { organization, login });
  if (res.status === 422) throw new ApiError("Enter your organization and email.", "VALIDATION_FAILED", 422);
}

export async function resetPassword(token: string, password: string): Promise<void> {
  const res = await publicPost("reset-password", { token, password });
  if (res.ok) return;
  const body = (await res.json().catch(() => ({}))) as { fields?: string[] };
  if (res.status === 422 && body.fields?.[0] === "password") {
    throw new ApiError("That password is too short or too easy to guess. Use a longer one that is not your email.", "VALIDATION_FAILED", 422);
  }
  throw new ApiError("This link has expired or was already used. Ask for a new one.", "BAD_LINK", res.status);
}

export async function resendVerification(): Promise<"sent" | "wait"> {
  try {
    await request("auth/resend-verification", { method: "POST", body: "{}" });
    return "sent";
  } catch (e) {
    if (e instanceof ApiError && e.status === 429) return "wait";
    throw e;
  }
}

export interface EmailNotices { enabled: boolean; can_email: boolean }

/** Does this patient get short emails about their care, and could one be sent (a confirmed address)? */
export const getEmailNotices = async () => (await request<EmailNotices>("my/email-notices")).body;

export const setEmailNotices = async (enabled: boolean) =>
  (await request<EmailNotices>("my/email-notices", { method: "POST", body: JSON.stringify({ enabled }) })).body;

export const getEmailStatus = async () => (await getMe()).email;

/** Set or change this person's email address (the current password is required). */
export async function setEmail(email: string, currentPassword: string) {
  try {
    const { body } = await request<{ email: string; verified: boolean; sent: boolean }>("auth/email", {
      method: "POST", body: JSON.stringify({ email, current_password: currentPassword }),
    });
    return body;
  } catch (e) {
    throw friendlyEmailError(e);
  }
}
