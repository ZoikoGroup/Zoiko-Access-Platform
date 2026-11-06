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
  late: { hours: number; bookings: number; messages: number };
  today: { confirmed: number; waiting: number; still_to_come: number };
  attention: { bookings_to_confirm: number; family_requests: number; waiting_list: number; referrals_to_decide: number; messages: number };
}

export interface Directory {
  organizations: { id: string; name: string; status: string }[];
  staff: {
    id: string; login: string; name?: string; type: string; status: string;
    roles: { role: string; organization_id: string; organization_name: string; status: string }[];
    pending_invite: boolean;
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
  rescheduled_from: string | null;
  attendance: "attended" | "no_show" | null;
  cancel_reason?: string | null;
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
  relationship: string;
  person: string;
  name: string;
  birth_date: string;
  requested_at: string;
  requested_by: string;
  requested_by_name: string | null;
}

export interface MessageThread {
  id: string;
  subject: string;
  status: "open" | "closed";
  patient: string;
  patient_name?: string | null;
  last_message_at: string;
  unread: number;
}

export interface ThreadView extends MessageThread {
  messages: { id: string; side: "patient" | "staff"; body: string; at: string; mine: boolean; from: string }[];
}

export interface Timing {
  count: number;
  median: number | null;               // hours
  average: number | null;              // hours
}

export interface Reports {
  forecast?: {
    enough_history: boolean; history_days: number;
    days: { date: string; weekday: number; booked: number; open: number; usual: number | null }[];
  };
  days: number;
  from: string;
  to: string;
  bookings: {
    per_day: { date: string; count: number }[];
    total: number;
    status: { requested: number; confirmed: number; cancelled: number };
    attendance: { attended: number; no_show: number; not_recorded: number };
    busiest_services: { name: string; count: number }[];
    hours_to_confirm: Timing;
    cancel_reasons: Record<string, number>;
    by_weekday: number[];                 // Monday first: appointments in the period, by the clinic's own day
    by_doctor: { provider_ref: string; name: string; attended: number; no_show: number;
      not_recorded: number; no_show_rate: number | null }[];
  };
  referrals: { received: number; issued: number; accepted: number; needs_info: number; declined: number };
  waiting_list: { joined: number; booked: number; left: number; waiting_now: number };
  messages: { started: number; closed: number; waiting_for_clinic: number; hours_to_first_reply: Timing };
}

export interface WorkItem {
  id: string;
  kind: "confirm_booking" | "decide_referral" | "answer_message";
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
  reminder_lead_hours: number;
  waitlist_offer_hours: number;
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
}
