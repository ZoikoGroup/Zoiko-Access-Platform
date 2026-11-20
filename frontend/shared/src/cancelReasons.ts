/** Why an appointment was cancelled. The codes are the server's; the server refuses a code from
 *  the wrong side, so a patient is offered only the first list and clinic staff the second. */
export const PATIENT_REASONS: Record<string, string> = {
  schedule_conflict: "I cannot make that time",
  no_longer_needed: "I no longer need it",
  booked_elsewhere: "I booked somewhere else",
  other: "Another reason",
};

export const CLINIC_REASONS: Record<string, string> = {
  clinic_unavailable: "The clinic is closed or unavailable then",
  doctor_unavailable: "The doctor is unavailable then",
  service_changed: "The service has changed",
  other: "Another reason",
};

/** Any code in plain words ("not given" for none). */
export const reasonText = (code: string | null | undefined): string =>
  !code ? "not given" : PATIENT_REASONS[code] ?? CLINIC_REASONS[code] ?? code;
