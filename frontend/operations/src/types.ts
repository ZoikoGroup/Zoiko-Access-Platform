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

/** Refused access checks over the last few days: who was refused most, and a count per day. */
export interface RefusedSummary {
  days: number;
  by_person: { actor: string; denied: number; total: number }[];
  by_day: { day: string; denied: number; total: number }[];
}

export interface Compliance extends Evidence {
  refused: RefusedSummary;
}

export interface Dashboard extends Evidence {
  tenant: string;
  counts: { patients: number; organizations: number; staff: number; roles_in_force: number; care_links: number };
  late: { hours: number; bookings: number; messages: number; data_requests: number };
  today: { confirmed: number; waiting: number; still_to_come: number };
  attention: { bookings_to_confirm: number; family_requests: number; waiting_list: number; referrals_to_decide: number; messages: number; data_requests: number; workflows_without_owner: number; partner_results: number; partner_on_hold: number; partner_held_mine: number; partner_hold_escalated: number; webhooks_gave_up: number };
}

export interface Directory {
  organizations: { id: string; name: string; status: string }[];
  staff: {
    id: string; login: string; name?: string; type: string; status: string;
    roles: { role: string; organization_id: string; organization_name: string; status: string }[];
    pending_invite: boolean;
    two_step_on?: boolean;
  }[];
  can_manage: boolean;
  me: string;
  grantable_roles: { code: string; label: string }[];
}

export interface PatientRow {
  id: string;
  patient_name?: string | null;
  status: string;
  identity_status: string;
  /** A possible duplicate of this patient is waiting for a decision. */
  under_review?: boolean;
  /** Confirmed to be the same person as another record (nothing has been merged). */
  same_as_another?: boolean;
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
  /** Features the organization's country pack has not switched on (DEV-383). */
  switched_off?: string[];
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
  rescheduled_from: string | null;
  attendance: "attended" | "no_show" | null;
  cancel_reason?: string | null;
  pending_change?: "cancel" | "rebook" | null;   // a change the patient asked for, not yet answered
  late?: boolean;
  patient_name?: string | null;
}

export interface CoverageRow {
  id: string;
  person: string;
  insurer: string;
  plan: string;
  member_last4: string;
  valid_from: string | null;
  valid_until: string | null;
  status: "active" | "ended";
  in_force: boolean;
}

export interface FamilyRequest {
  id: string;
  status: string;
  scopes?: string[];
  valid_until?: string | null;
  relationship: string;
  person: string;
  name: string;
  birth_date: string;
  requested_at: string;
  requested_by: string;
  requested_by_name: string | null;
  proof_kind?: string;
  proof_ref?: string;
}

export interface MessageThread {
  id: string;
  subject: string;
  status: "open" | "closed";
  patient: string;
  patient_name?: string | null;
  last_message_at: string;
  unread: number;
  unread_for_me?: number;                // what THIS login has not opened (others' openings do not count)
}

export interface ThreadView extends MessageThread {
  messages: { id: string; side: "patient" | "staff"; body: string; at: string; mine: boolean; from: string; seen_at?: string | null }[];
}

/** A count, or null when it is fewer than the small-number threshold and so not shown (DEV-389). */
export type Count = number | null;

export interface Timing {
  count: Count;
  median: number | null;               // hours
  average: number | null;              // hours
}

/** What one report number means (GHE-10: no number without an approved definition, DEV-389). */
export interface ReportMeasure {
  key: string; title: string; counts: string; out_of: string; leaves_out: string;
  covers: string[]; version: string; approved: boolean;
}

export interface Reports {
  forecast?: {
    enough_history: boolean; history_days: number;
    days: { date: string; weekday: number; booked: number; open: number; usual: number | null }[];
  } | null;
  days: number;
  from: string;
  to: string;
  /** The clock the days run on: the time zone this clinic's places share, else UTC (see zone_source). */
  zone: string;
  zone_source: "places" | "mixed" | "none";
  /** A section is null when its measure is not approved (withheld); a number is null when small. */
  bookings: {
    per_day: { date: string; count: Count }[] | null;
    total: Count;
    status: { requested: Count; confirmed: Count; cancelled: Count } | null;
    attendance: { attended: Count; no_show: Count; not_recorded: Count } | null;
    busiest_services: { name: string; count: Count }[] | null;
    hours_to_confirm: Timing | null;
    cancel_reasons: Record<string, Count> | null;
    by_weekday: Count[] | null;           // Monday first: appointments in the period, by the clinic's own day
    by_doctor: { provider_ref: string; name: string; attended: Count; no_show: Count;
      not_recorded: Count; no_show_rate: number | null }[] | null;
  };
  referrals: { received: Count; issued: Count; accepted: Count; needs_info: Count; declined: Count } | null;
  waiting_list: { joined: Count; booked: Count; left: Count; waiting_now: Count } | null;
  messages: { started: Count; closed: Count; waiting_for_clinic: Count; hours_to_first_reply: Timing | null } | null;
  measures?: ReportMeasure[];
  small_cell_k?: number;
  suppressed?: string[];
  withheld?: string[];
  evidence?: string;
}

export interface WorkItem {
  id: string;
  kind: "confirm_booking" | "decide_referral" | "answer_message" | "answer_data_request" | "settle_partner_op";
  label: string;
  subject: string;
  created_at: string;
  due_at: string;
  overdue: boolean;
  assigned_to: string;
  assigned_to_name: string;
  status: "open" | "done" | "cancelled";
  closed_at: string | null;
}

export interface ClinicSettings {
  response_target_hours: number;
  response_target_default?: number;       // what it is when the clinic has not chosen
  can_set_target?: boolean;               // only the clinic administrator can change it
  reminder_lead_hours: number;
  waitlist_offer_hours: number;
  waitlist_offer_default?: number;        // what it is when the clinic has not chosen (DEV-366)
  late_email_recipients: number;
}

export interface NameCheck {
  state: "none" | "verified" | "name_changed" | "withdrawn";
  basis?: string; basis_text?: string; note?: string; checked_on?: string; reason?: string;
}

export interface ProviderRow {
  id: string;
  name: string;
  name_check: NameCheck;
  upcoming: number;
  open_times: number;
  next_local: string | null;
  next_zone: string | null;
}

export interface Profile {
  person: string;
  name: string;
  birth_date: string;
  phone: string;
}

export interface WaitingRow {
  id: string;
  patient: string;                       // an id: names are not stored yet
  state: "waiting" | "offered";
  patient_name?: string | null;
  place: number;                         // 1 = waiting longest for that service
  joined_at: string;
  offered_at: string | null;
  service: { id: string; name: string };
  organization: { id: string; name: string };
  /** 0 routine, 1 priority, 2 urgent: set by clinic staff, never shown to the patient (DEV-366). */
  priority?: 0 | 1 | 2;
  /** A code from the fixed list (never free text), or "" when routine. */
  priority_reason?: string;
}

export interface FreeTime {
  id: string;
  starts_at: string;
  local_start: string;
  local_zone: string;
  location: string;
  provider: { id: string; name: string } | null;
}

export interface ServiceRow {
  id: string;
  name: string;
  category: string;
  description: string;
  duration_minutes: number;
  mode: string;
  status: "active" | "inactive";
  listing_state: "draft" | "evidence_pending" | "reviewed" | "published" | "suspended";
  listing?: { evidence_note: string; requested_by: string; reviewed_by: string; you_requested: boolean; can_approve: boolean };
  organization: { id: string; name: string };
  next_slot_local: string | null;
  next_slot_zone: string | null;
  waiting?: number;                 // people on the waiting list for this service
}

export interface LocationRow {
  id: string;
  name: string;
  address: string;
  phone: string;
  time_zone: string;
  organization: string;
}

export interface WorkflowInstance {
  public_id: string;
  definition: string;
  definition_version: number;
  journey: string;
  subject_ref: string;
  state: string;
  overlay: "clear" | "at_risk" | "exception" | "reconciling";
  owner: string;
  owner_name?: string;
  owner_inactive?: boolean;
  version: number;
  open_escalations: number;
  timers: { kind: string; due_at: string; watches: string }[];
  path: string[];
}

export interface WorkflowStep {
  seq: number;
  kind: "transition" | "overlay" | "owner" | "timer";
  from_state: string;
  to_state: string;
  overlay: string;
  actor: string;
  actor_name: string;
  evidence: { kind: string; ref: string }[];
  note: string;
  occurred_at: string;
}

export interface WorkflowHistory {
  instance: WorkflowInstance;
  chain_ok: boolean;
  bad_seq: number | null;
  steps: WorkflowStep[];
}

export interface Escalation {
  public_id: string;
  instance: string;
  owner: string;
  status: "open" | "reached";
  attempts: number;
  created_at: string;
}

export interface FeeRow {
  service_ref: string;
  name: string;
  current: { amount_minor: number; currency: string; effective_from: string } | null;
  upcoming: { amount_minor: number; currency: string; effective_from: string } | null;
}

export interface InvoiceRow {
  public_id: string;
  number: string;
  person_ref: string;
  patient_name?: string | null;
  currency: string;
  lines: { service_ref: string; name: string; quantity: number; unit_minor: number }[];
  total_minor: number;
  credited_minor: number;
  balance_minor: number;
  status: "draft" | "issued" | "paid" | "void";
  due_on: string | null;
  payment_ref: string;
  credit_notes: { number: string; amount_minor: number; reason: string; at: string }[];
  history?: { seq: number; kind: string; actor: string; at: string }[];
  chain_ok?: boolean;
}

export interface PartnerClient {
  public_id: string;
  label: string;
  key_id: string;
  allowed_types: string[];
  state: "pending" | "active" | "suspended" | "revoked";
  rate_per_minute: number;
  created_at: string;
  checks: { identity: boolean; legal: boolean; security: boolean };    // what Zoiko has recorded so far
  last_used_at: string | null;
  secret?: string;                  // only in the answer to "issue the secret" and "new secret"
}

export interface InboundEvent {
  public_id: string;
  client: string;
  client_label: string;
  delivery_id: string;
  event_type: string;
  state: "received" | "quarantined";
  reason: string;
  body_digest: string;
  created_at: string;
}

export interface WebhookEndpoint {
  public_id: string;
  label: string;
  url: string;
  topics: string[];
  key_id: string;
  state: "pending_verification" | "active" | "paused" | "revoked";
  created_at: string;
  verified_at: string | null;
  failure_streak: number;
  /** Zoiko's approval of the address (DEV-384): nothing is sent until it is "approved". */
  address_permit?: "none" | "proposed" | "approved" | "refused" | "revoked";
  secret?: string;                  // only in the answer to "add" and "new secret"
}

export interface WebhookDelivery {
  public_id: string;
  endpoint: string;
  event_type: string;
  delivery_id: string;
  state: "pending" | "retrying" | "delivered" | "dead_letter";
  attempts: number;
  next_attempt_at: string;
  last_status: number | null;
  last_error: string;
  created_at: string;
  delivered_at: string | null;
  owner_name?: string;
  policy_digest?: string;
  body_digest?: string;
}

export interface RightsCase {
  id: string;
  ref: string;
  type: string;
  state: "received" | "in_progress" | "completed" | "declined" | "withdrawn";
  note: string;
  created_at: string;
  identity_checked_at: string | null;
  decision_note: string;
  decided_at: string | null;
  due_at: string | null;
  due_basis: string;
  patient: string;
  patient_name?: string | null;
  lawful_hold: boolean;
  hold_reason?: string;
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
  patient_name?: string | null;
  referrer: { id: string; name: string };
  from_organization: { id: string; name: string };
  service: { id: string; name: string };
  organization: { id: string; name: string };
  acknowledged_at: string | null;        // staff saw it: this is NOT acceptance of care
  escalated: boolean;                    // no answer for too long: it goes to the fallback
  owner: { id: string; name: string } | null;
  responsible: { id: string; name: string };
}

export interface ReferralInbox {
  owner: { id: string; name: string } | null;
  fallback: { id: string; name: string } | null;
  doctors: { id: string; name: string }[];
  can_manage: boolean;
}

export type ExchangeState = "requested" | "accepted" | "declined" | "in_progress" | "result_available" | "reviewed" | "cancelled";
export type ClaimState = "submitted" | "approved" | "declined" | "paid" | "withdrawn";
export interface ClaimAttachment {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  sha256: string;
  scan: "pending" | "clean" | "infected";
  uploaded_by_side: "clinic" | "payer";
  created_at: string;
}

export interface Claim {
  id: string; claim_no: string; payer: string; payer_ref: string; patient: string; patient_name?: string | null;
  member_tail: string; category: string; label: string; amount_minor: number; currency: string; service_date: string;
  state: ClaimState; decision_reason: string | null; payment_ref: string | null; created_at: string;
  amount_paid_minor?: number | null; paid_on?: string | null; remittance_advice?: string | null;
  decided_at: string | null; paid_at: string | null;
  attachments?: ClaimAttachment[];
}

export interface ExchangeRequest {
  id: string; ref_no: string; kind: "lab_order" | "pharmacy_request" | "homecare_referral";
  partner: string; partner_ref: string; token: string; patient: string; patient_name?: string | null;
  label: string; urgency: "routine" | "urgent"; state: ExchangeState; decision_note: string;
  result_ref: string; on_hold: boolean; hold_owner_name?: string; hold_mine?: boolean; hold_escalated?: boolean; consent: "pending" | "granted" | "declined" | "withdrawn" | "expired"; created_at: string; result_at: string | null; reviewed_at: string | null;
}

export interface FhirIssue {
  path: string;
  code: string;
  text: string;
}

export type FhirQuarantineState = "held" | "discarded" | "resend_requested" | "released";

export interface FhirQuarantineItem {
  public_id: string;
  delivery_id: string;
  partner_label: string;
  partner_client_id: string;
  resource_type: string;
  partner_ref: string;
  body_digest: string;
  issues: FhirIssue[];
  state: FhirQuarantineState;
  created_at: string;
  decided_at: string | null;
  decided_by: string;
  decision_note: string;
}

/** A clinic's working days and opening times (0 = Monday), or null when it has not set any (DEV-367). */
export interface BusinessHours { days: number[]; opens: string; closes: string; holidays: string[] }
export interface BusinessHoursData {
  business_hours: BusinessHours | null; zone: string; zone_source: string; can_set: boolean;
}
