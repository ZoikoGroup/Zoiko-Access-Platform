import { finishSignIn } from "@shared/signInStep";
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
  return finishSignIn<LoginReply>(body);   // asks for a code if one is owed
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
export const addCustomer = (body: { customer: string; site: string; admin_email: string; kind?: string }) =>
  v1<{ customer: { id: string; name: string; site: string; kind: string } }>("platform/customers", {
    method: "POST", body: JSON.stringify(body),
  });

export const getOverview = () => v1<Overview>("platform/overview");

/** Support opens or closes a customer's patient self-sign-up. */
export const setCustomerSignUp = (customerId: string, enabled: boolean) =>
  v1<{ customer: { id: string; name: string; self_registration: boolean } }>(
    `platform/customers/${encodeURIComponent(customerId)}/sign-up`,
    { method: "POST", body: JSON.stringify({ enabled }) });

/** Support records the country a customer operates in (an ISO code, or "" to clear it). */
export const setCustomerCountry = (customerId: string, country: string) =>
  v1<{ customer: { id: string; name: string; country: string } }>(
    `platform/customers/${encodeURIComponent(customerId)}/country`,
    { method: "POST", body: JSON.stringify({ country }) });

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
  continuity_plan: string;             // who looks after patients still in the middle of something
  open_items: number;
}

/** Suspending or reinstating a customer takes two different support people. */
export const getSuspensions = async () => (await v1<{ suspensions: SuspensionRow[] }>("platform/suspensions")).suspensions;
export const requestSuspension = (customerId: string, action: "suspend" | "reinstate", reason: string, continuity_plan = "") =>
  v1<{ id: string }>(`platform/customers/${encodeURIComponent(customerId)}/suspension`, {
    method: "POST", body: JSON.stringify({ action, reason, continuity_plan }),
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
  severity: "sev0" | "sev1" | "sev2" | "sev3" | "unknown";
  happened_on: string;
  details: string;
  status: "open" | "resolved";
  resolution: string;
  resolved_on: string | null;
  recorded_by: string;
  resolved_by: string;
  assessed_by: string;
  assessment_note: string;
  safety_impact: boolean;
  handoff_note: string;
  handoff_by: string;
  // DEV-202: on call for it, and whether anyone has said "I have it"
  owner: string; deputy: string; owner_ref: string; deputy_ref: string;
  acknowledged_at: string | null; escalated_at: string | null;
  restoration_notice: string;
}

/** The hand-written incident log. Reading: support and safety staff; adding and closing: support only. */
export const getIncidents = async () => (await v1<{ incidents: IncidentRow[] }>("platform/incidents")).incidents;
export const addIncident = (body: { title: string; severity: string; happened_on: string; details: string; safety_impact: boolean }) =>
  v1<{ incident: IncidentRow }>("platform/incidents", { method: "POST", body: JSON.stringify(body) });
export const assessIncident = (id: string, severity: string, note: string) =>
  v1<{ incident: IncidentRow }>(`platform/incidents/${encodeURIComponent(id)}/assess`, {
    method: "POST", body: JSON.stringify({ severity, note }),
  });
export const recordHandoff = (id: string, note: string) =>
  v1<{ incident: IncidentRow }>(`platform/incidents/${encodeURIComponent(id)}/handoff`, {
    method: "POST", body: JSON.stringify({ note }),
  });
export const resolveIncident = (id: string, resolution: string, restoration_notice = "") =>
  v1<{ incident: IncidentRow }>(`platform/incidents/${encodeURIComponent(id)}/resolve`, {
    method: "POST", body: JSON.stringify({ resolution, restoration_notice }),
  });
export interface OnCallPerson { id: string; name: string }
export const getOnCallStaff = async () =>
  (await v1<{ staff: OnCallPerson[] }>("platform/incidents/on-call-staff")).staff;
export const assignIncident = (id: string, owner: string, deputy: string) =>
  v1<{ incident: IncidentRow }>(`platform/incidents/${encodeURIComponent(id)}/assign`, {
    method: "POST", body: JSON.stringify({ owner, deputy }),
  });
export const acknowledgeIncident = (id: string) =>
  v1<{ incident: IncidentRow }>(`platform/incidents/${encodeURIComponent(id)}/acknowledge`, { method: "POST", body: "{}" });
export interface TimelineEntry { kind: string; by: string; note: string; at: string }
export const getIncidentTimeline = async (id: string) =>
  (await v1<{ timeline: TimelineEntry[] }>(`platform/incidents/${encodeURIComponent(id)}/timeline`)).timeline;

export interface Indicator { value: number | null; target: number; burn_rate: number | null; meeting: boolean | null }
export interface SloReport {
  windows: { window_minutes: number; requests: number; availability: Indicator; latency: Indicator }[];
  routes: { route: string; requests: number; server_errors: number; mean_seconds: number }[];
  targets: { availability: number; latency_seconds: number; latency_share: number };
  limits: string[];
  texts: {
    provider: string; breaker: "open" | "closed"; failures_in_a_row: number; open_until: string | null;
    times_opened: number; queue: Record<string, number>; receipts: Record<string, number>;
  };
}
export const getSlo = () => v1<SloReport>("platform/slo");

export interface MatrixData {
  requirements: { public_id: string; source: string; req_id: string; group: string; title: string;
    status: "met" | "partly" | "not_built"; evidence: string }[];
  summary: Record<string, { total: number; met: number; partly: number; not_built: number }>;
  can_write: boolean;
}
export const getMatrix = (source: string, status: string) =>
  v1<MatrixData>(`platform/traceability?source=${encodeURIComponent(source)}&status=${encodeURIComponent(status)}`);
export const updateRequirement = (id: string, status: string, evidence: string) =>
  v1<unknown>(`platform/traceability/${encodeURIComponent(id)}/update`,
    { method: "POST", body: JSON.stringify({ status, evidence }) });
export const importMatrixSeed = () =>
  v1<{ added: number; changed: number; unchanged: number }>("platform/traceability/import",
    { method: "POST", body: JSON.stringify({ seed: true }) });

export interface ReleaseReadiness {
  public_id: string;
  release_ref: string;
  build: string;
  cell: string;
  country: string;
  capabilities: string[];
  tuple_digest: string;
  author: string;
  decision: "no_go" | "g3_ready" | "g4_authorized";
  recorded_decision: "no_go" | "g3_ready" | "g4_authorized";
  blockers: string[];
  requirements: {
    gate: string; requirement: string; status: "signed" | "missing" | "expired" | "vetoed";
    signer: string; role: string; reference: string; expires_on: string | null; note: string;
  }[];
}
export const getReleasesReadiness = async () => (await v1<{ releases: ReleaseReadiness[] }>("platform/readiness")).releases;
export const createRelease = (body: { release_ref: string; build: string; cell: string; country: string; capabilities: string[] }) =>
  v1<{ release: ReleaseReadiness }>("platform/readiness", { method: "POST", body: JSON.stringify(body) });
export const releaseStep = (id: string, step: "sign" | "veto" | "evaluate", body: Record<string, unknown>) =>
  v1<{ release: ReleaseReadiness }>(`platform/readiness/${encodeURIComponent(id)}/${step}`, { method: "POST", body: JSON.stringify(body) });

export interface PackManifest {
  settings: { data_request_days: number | null; emergency_number: string; default_language: string; time_zone: string };
  capabilities: Record<string, boolean>;
  evidence: Record<string, string>;
  validity: { expires_on: string | null };
}
export interface PackRow {
  id: string;
  pack_ref: string;
  country: string;
  service: string;
  revision: number;
  status: "draft" | "validated" | "expert_review" | "signed" | "suspended" | "retired";
  manifest: PackManifest;
  content_hash: string;
  note: string;
  author: string;
  created_at: string;
  approvals: Record<string, { by: string; at: string }>;
  missing: string[];
  expired: boolean;
  signature: { key_id: string; valid: boolean } | null;   // DEV-210: null unless signed
}
/** Per country with a pack: what a signed, current pack switches on now, and for how many customers. */
export interface PackCountry { country: string; on: string[]; customers: number }
export interface PacksData { packs: PackRow[]; enforced: boolean; countries: PackCountry[] }
export const getPacksData = () => v1<PacksData>("platform/packs");
export const getPacks = async () => (await getPacksData()).packs;
export const createPack = (body: { pack_ref: string; country: string; service: string; manifest: PackManifest }) =>
  v1<{ pack: PackRow }>("platform/packs", { method: "POST", body: JSON.stringify(body) });
export const packStep = (id: string, step: string, body: Record<string, unknown> = {}) =>
  v1<{ pack: PackRow }>(`platform/packs/${encodeURIComponent(id)}/${step}`, { method: "POST", body: JSON.stringify(body) });

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

/** A customer's partner waits for three checks by Zoiko support before it can have a secret. */
export type PartnerCheckName = "identity" | "legal" | "security";
export interface PartnerCheckRow {
  public_id: string; customer_ref: string; customer_name: string; label: string; key_id: string;
  allowed_types: string[]; checks: Record<PartnerCheckName, boolean>; created_at: string;
}
export const getPartnerChecks = async () => (await v1<{ partners: PartnerCheckRow[] }>("platform/partner-checks")).partners;
export const recordPartnerCheck = (customer: string, client: string, check: PartnerCheckName, note: string) =>
  v1<{ client: unknown }>(`platform/partner-checks/${encodeURIComponent(customer)}/${encodeURIComponent(client)}/${check}`, {
    method: "POST", body: JSON.stringify({ note }),
  });

// Encryption keys and outside sign-in options (read only: changed by a command on the server).
export interface KeysData {
  ok: boolean;
  provider: "env" | "file" | "wrapped" | string;
  current: string;
  keys: { id: string; fingerprint: string; current: boolean }[];
  warnings: string[];
  sso: { key: string; name: string }[];
}
export const getKeys = () => v1<KeysData>("platform/keys");
/** What each capability is for (GHE-08 AT01): proposed by staff, approved by a different safety officer. */
export interface PurposeRow {
  public_id: string; capability: string; version: number; statement: string;
  classification: "administrative" | "clinical_support" | "regulated";
  intended_users: string; legal_manufacturer: string;
  status: "draft" | "approved" | "superseded" | "retired"; note: string; created_at: string;
  reclassifies: boolean; reviews: number; reviews_needed: number;
}
export const getPurposes = async () => v1<{ purposes: PurposeRow[]; capabilities: string[] }>("platform/purposes");
export const proposePurpose = (body: {
  capability: string; statement: string; classification: string; intended_users: string; legal_manufacturer: string;
}) => v1<{ purpose: PurposeRow }>("platform/purposes", { method: "POST", body: JSON.stringify(body) });
export const purposeStep = (id: string, step: "approve" | "retire", reason = "") =>
  v1<{ purpose: PurposeRow }>(`platform/purposes/${encodeURIComponent(id)}/${step}`, {
    method: "POST", body: JSON.stringify({ reason }),
  });

// ------------------------------------------------------------------ partner certification (DEV-230)
export interface ConnectorCert {
  public_id: string; connector_key: string; profile_version: string;
  stage: "sandbox" | "certified" | "live" | "suspended";
  enrolled_by: string; certified_by: string; live_by: string; note: string; created_at: string;
}
export interface ConnectorRow {
  connector_key: string; profile_version: string; simulated: boolean; certification: ConnectorCert | null;
  source?: "built_in" | "plugin" | "http";
}
export interface NotLoaded { name: string; reason: string }
export const getConnectors = () =>
  v1<{ connectors: ConnectorRow[]; can_write: boolean; can_attest: boolean; not_loaded?: NotLoaded[] }>("platform/connectors");
export const connectorStep = (key: string, step: "enrol" | "certify" | "live" | "suspend", body: Record<string, unknown> = {}) =>
  v1<{ certification: ConnectorCert }>(`platform/connectors/${encodeURIComponent(key)}/${step}`, { method: "POST", body: JSON.stringify(body) });

// Partners' local codes and their SNOMED CT match (DEV-322).
export interface CodeMapping {
  id: string; source_system: string; source_code: string; source_display: string; target_code: string | null;
  state: "unmapped" | "proposed" | "approved" | "rejected"; seen_count: number; last_seen_at: string | null;
  proposed_by: string | null; proposed_by_name: string; decided_by: string | null; decided_by_name: string;
  decided_at: string | null; reject_reason: string | null;
}
export const getCodeMaps = () =>
  v1<{ mappings: CodeMapping[]; me: string; can_propose: boolean; can_decide: boolean }>("platform/codemaps");
export const codeMapStep = async (id: string, step: "propose" | "approve" | "reject", body: { target_code?: string; reason?: string } = {}) =>
  (await v1<{ mapping: CodeMapping }>(`platform/codemaps/${encodeURIComponent(id)}/${step}`, { method: "POST", body: JSON.stringify(body) })).mapping;

// Regional support's view of delivery attempts (GHE-13 T-54, DEV-328): status only.
export interface SupportAttempt {
  public_id: string; event_type: string; state: string; attempts: number; next_attempt_at: string;
  last_status: number | null; last_error: string | null; created_at: string; delivered_at: string | null;
}
export interface AttemptCustomer {
  id: string; name: string; status: string; counts: Record<string, number>; attempts: SupportAttempt[]; fhir_held: number;
}
export const getDeliveryAttempts = async () => (await v1<{ customers: AttemptCustomer[] }>("platform/delivery-attempts")).customers;

/** How the account page's security cards call the API as this person. */
export const securityCall = <T,>(path: string, body?: unknown): Promise<T> =>
  v1<T>(path, body === undefined ? {} : { method: "POST", body: JSON.stringify(body) });
