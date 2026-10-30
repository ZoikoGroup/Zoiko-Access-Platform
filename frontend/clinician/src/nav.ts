// The doctor app menu. `live` screens show real backend data; the rest say what they wait for.
export type PageId = "schedule" | "patients" | "notes" | "referrals" | "messages";

export interface NavItem {
  id: PageId;
  label: string;
  icon: string;
  live: boolean;
  waitsFor?: string;
}

export const NAV: NavItem[] = [
  { id: "schedule", label: "My schedule", icon: "appointments", live: true },
  { id: "patients", label: "My patients", icon: "patients", live: true },
  { id: "notes", label: "Notes & results", icon: "services", live: false,
    waitsFor: "Clinical notes, orders and results need the clinical record service, a later phase. Nothing here yet is a medical record." },
  { id: "referrals", label: "Referrals", icon: "referrals", live: true },
  { id: "messages", label: "Messages", icon: "messages", live: false,
    waitsFor: "Secure messaging with patients and colleagues: a later step." },
];
