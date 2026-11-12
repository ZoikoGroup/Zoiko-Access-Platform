// Shapes returned by the staff console's partner connection, journey and integration endpoints
// (backend/api/console.py). References, states and codes only: no patient names or details.

export type OperationState =
  | "pending" | "sent" | "accepted" | "confirmed" | "rejected" | "unknown" | "dead";

export interface ConnectorOp {
  public_id: string;
  connector_key: string;
  operation_type: string;
  subject_ref: string;
  organization_ref: string;
  state: OperationState;
  attempts: number;
  polls: number;
  partner_ref: string;
  last_code: string;
  last_error: string;
  created_at: string;
  sent_at: string | null;
  finished_at: string | null;
  /** True only when the partner itself confirmed. A person settling it by hand is never this. */
  source_confirmed: boolean;
}

export interface JourneyException {
  public_id: string;
  instance_ref: string;
  kind: string;
  detail: string;
  status: "open" | "resolved";
  raised_at: string;
  resolved_at: string | null;
  subject_ref: string;
  journey: string;
  journey_state: string;
}

export interface ExceptionsData {
  can_act: boolean;
  connector: { attention: ConnectorOp[]; waiting: ConnectorOp[]; recent: ConnectorOp[] };
  journeys: JourneyException[];
}

export interface OperationHistory {
  operation_ref: string;
  state: OperationState;
  chain_ok: boolean;
  bad_seq: number | null;
  events: { seq: number; event_type: string; state_after: string; code: string; occurred_at: string; by: string }[];
}

export interface JourneyDefinition {
  public_id: string;
  key: string;
  version: number;
  state: "released" | "retired";
  body_digest: string;
  released_at: string;
  initial: string;
  states: string[];
  terminal: Record<string, string>;
  transitions: string[];
  timers: Record<string, number>;
}

export interface JourneyInstance {
  public_id: string;
  definition_key: string;
  definition_version: number;
  subject_ref: string;
  state: string;
  status: "running" | "completed" | "cancelled";
  owner_ref: string;
  zone: string;
  created_at: string;
  completed_at: string | null;
}

export interface JourneysData {
  definitions: JourneyDefinition[];
  instances: JourneyInstance[];
  by_state: Record<string, number>;
}

export interface JourneyHistory {
  instance_ref: string;
  chain_ok: boolean;
  bad_seq: number | null;
  steps: {
    seq: number; transition: string; from_state: string; to_state: string; by: string;
    actor_kind: string; evidence: { kind: string; ref: string }[]; occurred_at: string;
  }[];
}

export interface DeveloperDelivery {
  public_id: string;
  event_id: string;
  event_type: string;
  state: "pending" | "delivered" | "dead";
  attempts: number;
  last_status: number | null;
  last_error: string;
  delivered_at: string | null;
}

export interface DeveloperWebhook {
  public_id: string;
  url: string;
  event_prefixes: string[];
  status: "active" | "paused" | "disabled";
  dead_streak: number;
  deliveries: DeveloperDelivery[];
}

export interface DeveloperClient {
  public_id: string;
  name: string;
  environment: "sandbox" | "production";
  scopes: string[];
  /** How many requests a minute this client may make. */
  rate_limit_per_minute: number;
  status: "active" | "revoked";
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  webhooks: DeveloperWebhook[];
}

export interface DeveloperData {
  can_manage: boolean;
  clients: DeveloperClient[];
  scopes: string[];
  event_families: string[];
  default_rate_limit: number;
  max_rate_limit: number;
}

export interface BookingSystem {
  /** The partner connector that confirms this clinic's bookings; "" means the clinic confirms by hand. */
  system: string;
  options: { key: string; label: string }[];
  can_set: boolean;
}

// ---- identity matching and identity checks (backend/api/identity.py) -------------------------------
export type MatchState = "possible_match" | "matched_pending_auth" | "linked" | "split_review" | "closed_not_same";
export type MatchFactor = "same" | "close" | "transposed" | "differ" | "missing";

export interface MaskedPerson {
  id: string;
  at_this_clinic: boolean;
  /** All a reviewer sees of a patient: initials, birth year and the last two phone digits. */
  initials: string;
  birth_year: string;
  phone_end: string;
}

export interface MatchCase {
  public_id: string;
  ref_no: string;
  state: MatchState;
  factors: { name: MatchFactor; birth_date: MatchFactor; phone: MatchFactor };
  flags: string[];
  confidence: "high" | "medium";
  critical: boolean;
  recheck_requested: boolean;
  first_reviewer: string;
  you_agreed_first: boolean;
  created_at: string;
  updated_at: string;
  people: MaskedPerson[];
  history: { seq: number; action: string; state_after: string; note: string; occurred_at: string; by: string }[];
  chain_ok: boolean;
}

export interface MatchesData { cases: MatchCase[]; can_decide: boolean }

export interface IdentityInfo {
  status: "verified" | "unverified";
  checked_on: string | null;
  valid_until: string | null;
  clinic: string;
  /** Why a patient is not verified right now ("" when they are or were never checked). */
  why_not: "" | "withdrawn" | "check_failed" | "expired" | "details_changed";
  /** Staff only: a possible duplicate of this patient is waiting for a decision. */
  under_review?: boolean;
}

export interface IdentityCheck {
  id: string;
  evidence_type: string;
  outcome: "passed" | "failed";
  clinic: string;
  by: string;
  checked_at: string;
  valid_until: string | null;
  withdrawn: boolean;
  withdraw_reason: string;
}

export interface IdentityData { identity: IdentityInfo; history?: IdentityCheck[] }
