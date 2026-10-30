// The only file that talks to the backend. Pages call these functions, never fetch().
import type {
  IncomingReferral,
  BookingRow, Dashboard, Directory, Evidence, Health, LocationRow, Me, PatientRow, ServiceRow,
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
export const cancelBooking = (id: string) => post(`bookings/${encodeURIComponent(id)}/cancel`);
export const listStaffServices = async () => (await v1<{ services: ServiceRow[] }>("services?all=1")).services;
export const listLocations = async () => (await v1<{ locations: LocationRow[] }>("locations")).locations;

export const createService = (body: {
  organization: string; name: string; category: string; duration_minutes: number;
  mode: string; description: string;
}) => post("services", body);

export const setServiceStatus = (id: string, status: "active" | "inactive") =>
  post(`services/${encodeURIComponent(id)}/status`, { status });

export const createLocation = (body: {
  organization: string; name: string; time_zone: string; address: string;
}) => post("locations", body);

export const addSlots = (serviceId: string, body: {
  location: string; first_local: string; count: number; provider?: string;
}) => post<{ created: number }>(`services/${encodeURIComponent(serviceId)}/slots`, body);

export const listIncomingReferrals = async () => (await v1<{ referrals: IncomingReferral[] }>("referrals/incoming")).referrals;
export const decideReferral = (id: string, decision: "accepted" | "needs_info" | "declined", reason: string) =>
  post(`referrals/${encodeURIComponent(id)}/decide`, { decision, reason });
export const getMe = () => v1<Me>("me");
export const getDashboard = () => v1<Dashboard>("staff/dashboard");
export const getCompliance = () => v1<Evidence>("staff/compliance");
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
