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

export interface Note {
  id: string; person: string; author: string; author_name: string; mine: boolean;
  note_date: string; state: "draft" | "signed" | "superseded"; version: number;
  supersedes: string | null; superseded_by: string | null; amend_reason: string | null;
  guardian_hidden?: boolean; erased?: boolean;
  diagnoses?: Diagnosis[]; has_diagnoses?: boolean;
  medications?: Medication[]; has_medications?: boolean;
  signed_at: string | null; created_at: string; body?: string;
  addenda?: { id: string; author: string; added_at: string; body: string }[];
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
  decision_note: string; result_ref: string; result_flag?: "" | "normal" | "abnormal" | "critical"; has_summary?: boolean;
  result_version?: number; result_correction?: string | null; has_values?: boolean;
  /** The lab says this result is not final yet: it cannot be marked reviewed (DEV-370). */
  result_preliminary?: boolean;
  created_at: string; result_at: string | null; reviewed_at: string | null;
}

export interface ResultValue { name: string; code?: string; value: number | string; unit?: string; low?: number | null; high?: number | null; flag?: string }
export interface ResultVersion { version: number; flag: string; summary: string; values?: ResultValue[]; result_ref: string; correction: string | null; delivered_at: string | null; reviewed: boolean; replaced_at: string; preliminary?: boolean }
export interface ResultSummary {
  id: string; flag: string; summary: string; result_ref: string; delivered_at: string | null;
  version?: number; correction?: string | null; earlier?: ResultVersion[]; values?: ResultValue[];
  preliminary?: boolean;
}

/** A coded diagnosis on a visit note (DEV-344): a SNOMED CT concept, its words and a status. */
export interface Diagnosis { code: string; text: string; status: "confirmed" | "provisional" | "ruled_out" }

/** A structured active medication on a visit note (DEV-375): a dm+d / SNOMED CT concept, name, dose and status. */
export interface Medication { code: string; name: string; dose?: string; status: "active" | "suspended" | "stopped" }
