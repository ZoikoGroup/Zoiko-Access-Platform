// The only file that talks to the backend. Pages call these functions, never fetch().
import { friendlyEmailError } from "@shared/EmailCard";
import { friendlyNameError } from "@shared/NameCard";
import { friendlyPasswordError } from "@shared/ChangePassword";
import type { Health, Overview } from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly code?: string) {
    super(message);
  }
}

export interface Session {
  token: string;
  tenantName: string;
  login: string;
  roles: { role: string; organization: { id: string; name: string } }[];
}

const SESSION_KEY = "zoiko.platform.session.v1";
let current: Session | null = null;

export function loadSession(): Session | null {
  // DEV ONLY shortcut: /?tenant=<id>&as=<login id> uses the unsigned dev token (backend dev flags).
  const q = new URLSearchParams(window.location.search);
  const tenant = q.get("tenant");
  const subject = q.get("as");
  if (import.meta.env.DEV && tenant && subject) {
    setSession({
      token: `dev:${tenant}:dev-idp:${subject}`, tenantName: "Zoiko Platform", login: subject,
      roles: [{ role: "R11", organization: { id: "", name: "" } }],
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
  if (!(res.headers.get("Content-Type") ?? "").includes("json")) throw new ApiError("Cannot reach the server");
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
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${current.token}` },
  });
  if (!(res.headers.get("Content-Type") ?? "").includes("json")) {
    throw new ApiError("The server sent something that is not JSON (is the backend reachable?)");
  }
  const body = (await res.json()) as T & { title?: string; code?: string };
  if (!res.ok) throw new ApiError(body.title ?? `Request failed (${res.status})`, body.code);
  return body;
}

/** Each call is recorded in the platform tenant's own audit chain, so the page does not poll. */
/** Support adds a customer; its first administrator is emailed an invitation. */
export const addCustomer = (body: { customer: string; site: string; admin_email: string }) =>
  v1<{ customer: { id: string; name: string; site: string } }>("platform/customers", {
    method: "POST", body: JSON.stringify(body),
  });

export const getOverview = () => v1<Overview>("platform/overview");

/** Support opens or closes a customer's patient self-sign-up. */
export const setCustomerSignUp = (customerId: string, enabled: boolean) =>
  v1<{ customer: { id: string; name: string; self_registration: boolean } }>(
    `platform/customers/${encodeURIComponent(customerId)}/sign-up`,
    { method: "POST", body: JSON.stringify({ enabled }) });

export interface ReleaseRow {
  id: string;
  version: string;
  summary: string;
  details: string;
  released_on: string;
  recorded_by: string;
}

/** The hand-written release log. Reading is allowed to support and safety staff; adding to support only. */
export const getReleases = async () => (await v1<{ releases: ReleaseRow[] }>("platform/releases")).releases;
export interface SuspensionRow {
  id: string;
  customer: string;
  customer_name: string;
  kind: "suspend" | "reinstate";
  reason: string;
  status: "pending" | "applied" | "rejected" | "cancelled";
  requested_by: string;
  decided_by: string;
  decision_note: string;
  decided_at: string | null;
  created_at: string;
  mine: boolean;                       // asked by the person who is signed in
}

/** Suspending or reinstating a customer takes two different support people. */
export const getSuspensions = async () => (await v1<{ suspensions: SuspensionRow[] }>("platform/suspensions")).suspensions;
export const requestSuspension = (customerId: string, action: "suspend" | "reinstate", reason: string) =>
  v1<{ id: string }>(`platform/customers/${encodeURIComponent(customerId)}/suspension`, {
    method: "POST", body: JSON.stringify({ action, reason }),
  });
export const approveSuspension = (id: string) =>
  v1<{ id: string }>(`platform/suspensions/${encodeURIComponent(id)}/approve`, { method: "POST", body: "{}" });
export const rejectSuspension = (id: string, note: string) =>
  v1<{ id: string }>(`platform/suspensions/${encodeURIComponent(id)}/reject`, { method: "POST", body: JSON.stringify({ note }) });
export const cancelSuspension = (id: string) =>
  v1<{ id: string }>(`platform/suspensions/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" });

export interface IncidentRow {
  id: string;
  title: string;
  severity: "sev0" | "sev1" | "sev2" | "sev3";
  happened_on: string;
  details: string;
  status: "open" | "resolved";
  resolution: string;
  resolved_on: string | null;
  recorded_by: string;
  resolved_by: string;
}

/** The hand-written incident log. Reading: support and safety staff; adding and closing: support only. */
export const getIncidents = async () => (await v1<{ incidents: IncidentRow[] }>("platform/incidents")).incidents;
export const addIncident = (body: { title: string; severity: string; happened_on: string; details: string }) =>
  v1<{ incident: IncidentRow }>("platform/incidents", { method: "POST", body: JSON.stringify(body) });
export const resolveIncident = (id: string, resolution: string) =>
  v1<{ incident: IncidentRow }>(`platform/incidents/${encodeURIComponent(id)}/resolve`, {
    method: "POST", body: JSON.stringify({ resolution }),
  });

export const addRelease = (body: { version: string; summary: string; released_on: string; details: string }) =>
  v1<{ release: { id: string } }>("platform/releases", { method: "POST", body: JSON.stringify(body) });

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
  throw new ApiError("This link has expired or was already used. Ask for a new one.", "BAD_LINK");
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

/** The hazard register. Reading: support and safety staff; deciding: safety officers only. */
export interface HazardRow {
  id: string; ref_no: string; library: "product" | "ai"; title: string; hazardous_situation: string;
  exposure_path: string; affected_groups: string; severity: number; probability: number;
  basis_method: string; residual_risk: string; owner: string; linked_build: string;
  assessed_by: string; reviewer: string; status: "OPEN" | "VERIFIED" | "REJECTED" | "ACCEPTED" | "RETIRED";
  assessed: boolean;
}
export const getHazards = async () =>
  v1<{ me: string; can_decide: boolean; hazards: HazardRow[] }>("platform/hazards");
const hazardPost = (id: string, action: string, body: unknown) =>
  v1<{ hazard: HazardRow }>(`platform/hazards/${encodeURIComponent(id)}/${action}`, { method: "POST", body: JSON.stringify(body) });
export const assessHazard = (id: string, body: {
  severity: number; probability: number; residual_risk: string; basis_method: string; hazardous_situation: string;
}) => hazardPost(id, "assess", body);
export const reviewHazard = (id: string, verdict: "verified" | "rejected", note: string) =>
  hazardPost(id, "review", { verdict, note });
export const acceptHazard = (id: string, note: string) => hazardPost(id, "accept", { note });
export const retireHazard = (id: string, reason: string) => hazardPost(id, "retire", { reason });

// Country packs (read only: packs are installed by a command, never from a screen).
export interface PackRow {
  public_id: string; country: string; version: number; status: "active" | "superseded" | "revoked";
  valid_from: string; expires_at: string; revocation_epoch: number; key_id: string;
  capabilities: string[];
  /** True only if the pack counts RIGHT NOW: signature, dates and revocation all check out. */
  in_force: boolean;
}
export interface PacksData {
  packs: PackRow[]; revocation_floor: number; trusted_key_ids: string[];
  /** Whether features must be switched on by the country's pack, and which country this cell serves. */
  enforced: boolean; country: string; country_capabilities: string[];
}
export const getPacks = () => v1<PacksData>("platform/packs");
