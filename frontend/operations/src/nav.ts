// The menu from the design. `live` pages show real backend data today; the others are honest
// placeholders that say which backend step they wait for.
export type PageId =
  | "dashboard" | "patients" | "providers" | "organizations" | "team" | "services" | "appointments"
  | "referrals" | "workflows" | "messages" | "billing" | "compliance" | "reports"
  | "integrations" | "settings";

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
  { id: "providers", label: "Providers", icon: "providers", live: false,
    waitsFor: "Clinician profiles, qualifications and facility privileges (a later trust step)." },
  { id: "organizations", label: "Organizations", icon: "organizations", live: true },
  { id: "team", label: "Team", icon: "providers", live: true },
  { id: "services", label: "Services", icon: "services", live: true },
  { id: "appointments", label: "Appointments", icon: "appointments", live: true },
  { id: "referrals", label: "Referrals", icon: "referrals", live: true },
  { id: "workflows", label: "Workflows", icon: "workflows", live: false,
    waitsFor: "The workflow engine and task queues: planned (B8)." },
  { id: "messages", label: "Messages", icon: "messages", live: false,
    waitsFor: "Notifications and secure messages: planned (B9, comms)." },
  { id: "billing", label: "Billing & Claims", icon: "billing", live: false,
    waitsFor: "Entitlements, invoices and claims: a later phase." },
  { id: "compliance", label: "Compliance", icon: "compliance", live: true },
  { id: "reports", label: "Reports", icon: "reports", live: false,
    waitsFor: "Analytics and reports: a later phase." },
  { id: "integrations", label: "Integrations", icon: "integrations", live: false,
    waitsFor: "Partner connectors and webhooks: planned (B8, partner simulator)." },
  { id: "settings", label: "Settings", icon: "settings", live: false,
    waitsFor: "Tenant and policy settings: a later step." },
];
