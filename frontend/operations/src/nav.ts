// The menu from the design. `live` pages show real backend data today; the others are honest
// placeholders that say which backend step they wait for.
export type PageId =
  | "dashboard" | "patients" | "providers" | "organizations" | "team" | "services" | "appointments"
  | "waitlist" | "family" | "referrals" | "workflows" | "messages" | "billing" | "compliance" | "reports"
  | "integrations" | "ai" | "settings" | "rights" | "engine" | "exchange";

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
  { id: "providers", label: "Providers", icon: "providers", live: true },
  { id: "organizations", label: "Organizations", icon: "organizations", live: true },
  { id: "team", label: "Team", icon: "providers", live: true },
  { id: "services", label: "Services", icon: "services", live: true },
  { id: "appointments", label: "Appointments", icon: "appointments", live: true },
  { id: "waitlist", label: "Waiting list", icon: "appointments", live: true },
  { id: "family", label: "Family requests", icon: "patients", live: true },
  { id: "referrals", label: "Referrals", icon: "referrals", live: true },
  { id: "exchange", label: "Partner requests", icon: "referrals", live: true },
  { id: "workflows", label: "Work queue", icon: "workflows", live: true },
  { id: "messages", label: "Messages", icon: "messages", live: true },
  { id: "billing", label: "Billing", icon: "billing", live: true },
  { id: "compliance", label: "Compliance", icon: "compliance", live: true },
  { id: "rights", label: "Data requests", icon: "compliance", live: true },
  { id: "engine", label: "Workflow engine", icon: "workflows", live: true },
  { id: "reports", label: "Reports", icon: "reports", live: true },
  { id: "integrations", label: "Integrations", icon: "integrations", live: true },
  { id: "ai", label: "AI permits", icon: "settings", live: true },
  { id: "settings", label: "Settings", icon: "settings", live: true },
];
