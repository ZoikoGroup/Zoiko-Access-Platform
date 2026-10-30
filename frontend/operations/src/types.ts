// Shapes returned by the backend's /api/v1 staff endpoints.

export type Health = "ok" | "down" | "checking";

export interface Decision {
  at: string;
  actor: string;      // staff by login, patients as "Patient ab12cd"
  operation: string;
  purpose: string;
  allowed: boolean;
  reason: string;
}

export interface AuditEvent {
  seq: number;
  at: string;
  actor: string;
  action: string;
  object: string;
  outcome: string;
  reason: string;
  hash: string;
  prev: string;
}

export interface ChainStatus {
  ok: boolean;
  checked: number;
  problem: string;
  bad_seq: number | null;
}

/** Decisions, audit entries and the chain check. `evidence` is false if this role may not see them. */
export interface Evidence {
  evidence: boolean;
  decisions: Decision[];
  events: AuditEvent[];
  chain: ChainStatus | null;
}

export interface Dashboard extends Evidence {
  tenant: string;
  counts: { patients: number; organizations: number; staff: number; roles_in_force: number; care_links: number };
}

export interface Directory {
  organizations: { id: string; name: string; status: string }[];
  staff: {
    id: string; login: string; type: string; status: string;
    roles: { role: string; organization_id: string; organization_name: string; status: string }[];
    pending_invite: boolean;
  }[];
  can_manage: boolean;
  me: string;
  grantable_roles: { code: string; label: string }[];
}

export interface PatientRow {
  id: string;
  status: string;
  identity_status: string;
  has_login: boolean;
  care: { id: string; clinician: string; basis: string; status: string; contested: boolean }[];
  proxies: { id: string; guardian: string; type: string; status: string }[];
}

export interface Me {
  actor: string;
  type: string;
  tenant: string;
  cell: string;
  home: string;
}

// ---- /api/v1 shapes (staff screens) ----
export interface BookingRow {
  id: string;
  status: "requested" | "confirmed" | "cancelled";
  person: string;
  service: { id: string; name: string; mode: string };
  organization: { id: string; name: string };
  location: { id: string; name: string; address: string };
  starts_at: string;
  ends_at: string;
  local_start: string;
  local_zone: string;
}

export interface ServiceRow {
  id: string;
  name: string;
  category: string;
  description: string;
  duration_minutes: number;
  mode: string;
  status: "active" | "inactive";
  organization: { id: string; name: string };
  next_slot_local: string | null;
  next_slot_zone: string | null;
}

export interface LocationRow {
  id: string;
  name: string;
  address: string;
  time_zone: string;
  organization: string;
}

export interface IncomingReferral {
  id: string;
  patient: string;                       // an id: names are not stored yet
  state: "issued" | "accepted" | "needs_info" | "declined";
  urgency: "routine" | "urgent";
  reason: string;
  decision_reason: string;
  decided_at: string | null;
  created_at: string;
  referrer: { id: string; name: string };
  from_organization: { id: string; name: string };
  service: { id: string; name: string };
  organization: { id: string; name: string };
}
