// Shapes returned by the backend's /api/v1/clinician endpoints.

export type Health = "ok" | "down" | "checking";

export interface Appointment {
  id: string;
  status: "requested" | "confirmed" | "cancelled";
  person: string;                       // the patient, as an id: names are not stored yet
  service: { id: string; name: string; mode: string };
  organization: { id: string; name: string };
  location: { id: string; name: string; address: string };
  starts_at: string;
  ends_at: string;
  local_start: string;                  // the clinic's wall clock, e.g. 2027-09-01T09:00:00
  local_zone: string;
  attendance: "attended" | "no_show" | null;   // set by the clinic after the visit time
  patient_name?: string | null;
}

export interface PatientRow {
  id: string;
  patient_name?: string | null;
  basis: string;                        // why you may see them: appointment, referral, care team
  since: string;
  appointments: number;
}

export interface ServiceOption {
  id: string;
  name: string;
  organization: { id: string; name: string };
}

export interface Referral {
  id: string;
  patient: string;                       // the patient's id
  patient_name?: string | null;
  state: "issued" | "accepted" | "needs_info" | "declined";
  urgency: "routine" | "urgent";
  reason: string;
  decision_reason: string;               // why the clinic asked for more or declined
  decided_at: string | null;
  created_at: string;
  service: { id: string; name: string };
  organization: { id: string; name: string };
  escalated?: boolean;                   // nobody answered for too long
  responsible?: { id: string; name: string };
}

export interface Thread {
  id: string;
  subject: string;
  status: "open" | "closed";
  patient: string;                       // the patient's id
  patient_name?: string | null;
  last_message_at: string;
  unread: number;
  unread_for_me?: number;                // what THIS login has not opened
}

export interface ThreadView extends Thread {
  messages: { id: string; side: "patient" | "staff"; body: string; at: string; mine: boolean; seen_at?: string | null; from?: string }[];
}

export interface PartnerRequest {
  id: string; ref_no: string; kind: "lab_order" | "pharmacy_request" | "homecare_referral";
  partner: string; token: string; patient: string; patient_name?: string | null; label: string;
  urgency: "routine" | "urgent";
  state: "requested" | "accepted" | "declined" | "in_progress" | "result_available" | "reviewed" | "cancelled";
  decision_note: string; result_ref: string; created_at: string; result_at: string | null; reviewed_at: string | null;
}
