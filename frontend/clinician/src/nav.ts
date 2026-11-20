// The doctor app menu. `live` screens show real backend data; the rest say what they wait for.
export type PageId = "schedule" | "patients" | "notes" | "referrals" | "messages" | "results" | "account";

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
  { id: "notes", label: "Notes", icon: "services", live: true },
  { id: "referrals", label: "Referrals", icon: "referrals", live: true },
  { id: "results", label: "Orders & results", icon: "services", live: true },
  { id: "messages", label: "Messages", icon: "messages", live: true },
  { id: "account", label: "My account", icon: "settings", live: true },
];
