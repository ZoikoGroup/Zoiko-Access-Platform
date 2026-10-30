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
}

export interface Slot {
  id: string;
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_zone: string;
  location: string;
  provider: { id: string; name: string } | null;
}

export interface Booking {
  id: string;
  status: "requested" | "confirmed" | "cancelled";
  created_at: string;
  person: string;
  service: { id: string; name: string; mode: string };
  organization: { id: string; name: string };
  location: { id: string; name: string; address: string };
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_zone: string;
  provider: { id: string; name: string } | null;
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

export const getMe = async () => (await request<Me>("me")).body;

export const getServices = async (q = "") =>
  (await request<{ services: Service[] }>(`services${q ? `?q=${encodeURIComponent(q)}` : ""}`)).body.services;

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

export async function cancelBooking(id: string): Promise<Booking> {
  const { body } = await request<{ booking: Booking }>(`bookings/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: "{}",
  });
  return body.booking;
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
