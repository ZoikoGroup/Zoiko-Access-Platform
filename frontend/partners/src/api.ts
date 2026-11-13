// The only file that talks to the backend. Pages call these functions, never fetch().
import { friendlyEmailError } from "@shared/EmailCard";
import { friendlyNameError } from "@shared/NameCard";
import { friendlyPasswordError } from "@shared/ChangePassword";
import type { Dashboard, Delivery, Entitlement, Health, Hook, Incoming, PartnerKey, Overview, PartnerService, Plan, PlanMember, ProgramSummary, TeamMember } from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly code?: string) {
    super(message);
  }
}

export interface Session {
  token: string;
  tenantName: string;
  login: string;
  area: string;
  roles: { role: string; organization: { id: string; name: string } }[];
}

const SESSION_KEY = "zoiko.partners.session.v1";
let current: Session | null = null;

export function loadSession(): Session | null {
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

export const getOverview = () => v1<Overview>("portal/overview");
export const getMe = () => v1<{ cell: string; tenant: string }>("me");
export const getServices = async () => (await v1<{ services: PartnerService[] }>("services?all=1")).services;
export const addService = (body: { organization: string; name: string; category: string; duration_minutes: number; mode: string; description: string }) =>
  v1<{ service: PartnerService }>("services", { method: "POST", body: JSON.stringify(body) });
export const askForListing = (id: string, note: string) =>
  v1<{ service: PartnerService }>(`services/${encodeURIComponent(id)}/listing`, { method: "POST", body: JSON.stringify({ action: "request", note }) });

export const getTeam = async () => (await v1<{ team: TeamMember[] }>("portal/team")).team;
export const inviteColleague = (email: string) => v1<{ member: { id: string } }>("portal/team", { method: "POST", body: JSON.stringify({ email }) });
export const teamStep = (id: string, step: "suspend" | "reinstate" | "resend") =>
  v1<unknown>(`portal/team/${encodeURIComponent(id)}/${step}`, { method: "POST", body: "{}" });

export const getProgram = () => v1<ProgramSummary>("portal/program");
export const programStep = (step: "enrol" | "withdraw", refs: string[]) =>
  v1<{ added?: number; already_enrolled?: number; withdrawn?: number }>(`portal/program/${step}`, { method: "POST", body: JSON.stringify({ refs }) });

export const getPlans = async () => (await v1<{ plans: Plan[] }>("portal/plans")).plans;
export const addPlan = (body: { code: string; name: string; covered_categories: string[]; copay_percent: number }) =>
  v1<{ plan: Plan }>("portal/plans", { method: "POST", body: JSON.stringify(body) });
export const changePlan = (id: string, body: { active?: boolean; copay_percent?: number }) =>
  v1<{ plan: Plan }>(`portal/plans/${encodeURIComponent(id)}`, { method: "POST", body: JSON.stringify(body) });
export const getPlanMembers = async () => (await v1<{ members: PlanMember[] }>("portal/members")).members;
export const addPlanMember = (body: { plan: string; member_id: string; valid_from?: string; valid_until?: string }) =>
  v1<{ member: PlanMember }>("portal/members", { method: "POST", body: JSON.stringify(body) });
export const endPlanMember = (id: string) => v1<{ member: PlanMember }>(`portal/members/${encodeURIComponent(id)}/end`, { method: "POST", body: "{}" });
export const checkEntitlement = (member_id: string, category: string, on?: string) =>
  v1<Entitlement>("portal/entitlement", { method: "POST", body: JSON.stringify({ member_id, category, ...(on ? { on } : {}) }) });

export const getDashboard = () => v1<Dashboard>("portal/dashboard");

export const getKeys = async () => (await v1<{ clients: PartnerKey[] }>("staff/integrations/partners")).clients;
export const addKey = async (label: string, allowed_types: string[]) =>
  (await v1<{ client: PartnerKey }>("staff/integrations/partners", { method: "POST", body: JSON.stringify({ label, allowed_types }) })).client;
export const keyStep = async (id: string, step: "suspend" | "resume" | "revoke" | "rotate-secret") =>
  (await v1<{ client: PartnerKey }>(`staff/integrations/partners/${encodeURIComponent(id)}/${step}`, { method: "POST", body: "{}" })).client;
export const getHooks = async () => (await v1<{ endpoints: Hook[] }>("staff/integrations/webhooks")).endpoints;
export const addHook = async (label: string, url: string, topics: string[]) =>
  (await v1<{ endpoint: Hook }>("staff/integrations/webhooks", { method: "POST", body: JSON.stringify({ label, url, topics }) })).endpoint;
export const hookStep = async (id: string, step: "verify" | "pause" | "resume" | "revoke" | "rotate-secret") =>
  (await v1<{ endpoint: Hook }>(`staff/integrations/webhooks/${encodeURIComponent(id)}/${step}`, { method: "POST", body: "{}" })).endpoint;
export const getDeliveries = async () => (await v1<{ deliveries: Delivery[] }>("staff/integrations/deliveries")).deliveries;

export const getIncoming = async () => (await v1<{ requests: Incoming[] }>("exchange/incoming")).requests;
export const incomingStep = async (id: string, step: "accept" | "decline" | "start" | "deliver", extra: { note?: string; result_ref?: string } = {}) =>
  (await v1<{ request: Incoming }>(`exchange/incoming/${encodeURIComponent(id)}/${step}`, { method: "POST", body: JSON.stringify(extra) })).request;

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
  throw new ApiError("This link has expired or was already used. Ask Zoiko support to send a new one.", "BAD_LINK");
}

/** Change the signed-in person's password. The server ends every other session and returns a fresh
 *  token for this one, which replaces the stored token so this tab stays signed in. */
export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  try {
    const body = await v1<{ token: string }>("auth/change-password", {
      method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (current) setSession({ ...current, token: body.token });
  } catch (e) {
    throw friendlyPasswordError(e);
  }
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
export const getNameCheck = async () =>
  (await v1<{ name_check: import("@shared/Account").NameCheckInfo }>("me")).name_check;

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
