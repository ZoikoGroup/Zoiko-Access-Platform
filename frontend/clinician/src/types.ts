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
}
