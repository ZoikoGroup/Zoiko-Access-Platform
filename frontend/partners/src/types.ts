// Shapes returned by the backend for the partner, payer and employer portal.

export type Health = "ok" | "down" | "checking";

export interface Capability { resource: string; verbs: string[]; scope: string }

export interface PortalRole {
  code: string;
  name: string;
  workspace: string;
  prohibitions: string[];
  organization: { id: string; name: string };
  can: Capability[];
}

export interface Overview {
  area: "partner" | "payer" | "employer";
  tenant: string;
  roles: PortalRole[];
  connected: string[];
  waiting: string[];
}

export interface PartnerService {
  id: string;
  name: string;
  category: string;
  description: string;
  duration_minutes: number;
  mode: string;
  status: string;
  listing_state: string;
  organization: { id: string; name: string };
  listing?: { evidence_note: string; requested_by: string; reviewed_by: string; you_requested: boolean; can_approve: boolean };
}

export interface TeamMember { id: string; login: string; status: string; pending_invite: boolean; you: boolean; roles: string[] }
export interface Shown { value: number | null; hidden: boolean }
export interface ProgramSummary { min_group: number; enrolled: Shown; withdrawn: Shown }
export interface Plan { id: string; code: string; name: string; covered_categories: string[]; copay_percent: number; active: boolean }
export interface PlanMember { id: string; member: string; plan: string; valid_from: string | null; valid_until: string | null; state: string }
export interface Entitlement { covered: boolean; reason: string; plan: string | null; copay_percent: number | null }

export interface Dashboard {
  area: "partner" | "payer" | "employer" | "developer";
  team: number;
  requests?: { new: number; in_progress: number; delivered: number };
  services?: { total: number; listed: number; waiting_review: number; not_listed: number };
  to_review?: number;
  plans?: { plans: number; plans_on: number; members: number; members_ended: number };
  programme?: ProgramSummary;
  keys?: { active: number; total: number };
  webhooks?: { active: number; total: number };
  deliveries?: { recent: number; delivered: number; retrying: number; gave_up: number };
}

export interface PartnerKey {
  public_id: string; label: string; key_id: string; allowed_types: string[];
  state: "active" | "suspended" | "revoked"; rate_per_minute: number; created_at: string;
  last_used_at: string | null; secret?: string;
}
export interface Hook {
  public_id: string; label: string; url: string; topics: string[]; key_id: string;
  state: "pending_verification" | "active" | "paused" | "revoked"; verified_at: string | null;
  failure_streak: number; secret?: string;
}
export interface Delivery {
  public_id: string; endpoint: string; event_type: string; state: "pending" | "retrying" | "delivered" | "dead_letter";
  attempts: number; last_status: number | null; created_at: string;
}

export type IncomingState = "requested" | "accepted" | "declined" | "in_progress" | "result_available" | "reviewed" | "cancelled";
export interface Incoming {
  id: string; ref_no: string; kind: "lab_order" | "pharmacy_request" | "homecare_referral"; from: string;
  token: string; label: string; urgency: "routine" | "urgent"; state: IncomingState;
  decision_note: string; result_ref: string; on_hold: boolean; created_at: string; accepted_at: string | null; result_at: string | null;
}
