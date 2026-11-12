// The menu from the design. `live` pages show real backend data today; the others are honest
// placeholders that say which backend step they wait for.
export type PageId =
  | "dashboard" | "patients" | "providers" | "organizations" | "team" | "services" | "appointments"
  | "waitlist" | "family" | "referrals" | "workflows" | "messages" | "billing" | "compliance" | "reports"
  | "integrations" | "exceptions" | "journeys" | "matches" | "ai" | "settings";

export interface NavItem {
  id: PageId;
  label: string;
  icon: string;
  live: boolean;
  waitsFor?: string;
}

export const NAV: NavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard", live: true },
  { id: "patients", label: "Patients", icon: "patients", live: true },
  { id: "matches", label: "Duplicates", icon: "shield", live: true },
  { id: "providers", label: "Providers", icon: "providers", live: true },
  { id: "organizations", label: "Organizations", icon: "organizations", live: true },
  { id: "team", label: "Team", icon: "providers", live: true },
  { id: "services", label: "Services", icon: "services", live: true },
  { id: "appointments", label: "Appointments", icon: "appointments", live: true },
  { id: "waitlist", label: "Waiting list", icon: "appointments", live: true },
  { id: "family", label: "Family requests", icon: "patients", live: true },
  { id: "referrals", label: "Referrals", icon: "referrals", live: true },
  { id: "workflows", label: "Work queue", icon: "workflows", live: true },
  { id: "messages", label: "Messages", icon: "messages", live: true },
  { id: "billing", label: "Billing & Claims", icon: "billing", live: false,
    waitsFor: "Entitlements, invoices and claims: a later phase." },
  { id: "compliance", label: "Compliance", icon: "compliance", live: true },
  { id: "reports", label: "Reports", icon: "reports", live: true },
  { id: "exceptions", label: "Exceptions", icon: "shield", live: true },
  { id: "journeys", label: "Journeys", icon: "workflows", live: true },
  { id: "integrations", label: "Integrations", icon: "integrations", live: true },
  { id: "ai", label: "AI permits", icon: "settings", live: true },
  { id: "settings", label: "Settings", icon: "settings", live: true },
];
