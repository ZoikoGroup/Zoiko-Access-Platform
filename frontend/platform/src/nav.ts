// The platform console menu. `live` screens show real backend data; the rest say what they wait for.
export type PageId = "overview" | "customers" | "incidents" | "releases" | "settings";

export interface NavItem {
  id: PageId;
  label: string;
  icon: string;
  live: boolean;
  waitsFor?: string;
}

export const NAV: NavItem[] = [
  { id: "overview", label: "Overview", icon: "dashboard", live: true },
  { id: "customers", label: "Customers", icon: "organizations", live: true },
  { id: "incidents", label: "Incidents", icon: "shield", live: false,
    waitsFor: "Safety and security incident tracking: a later phase (severity scales are still undecided, DEV-014)." },
  { id: "releases", label: "Releases", icon: "integrations", live: false,
    waitsFor: "Release evidence and rollout control: a later phase." },
  { id: "settings", label: "Settings", icon: "settings", live: false,
    waitsFor: "Customer onboarding, suspension and platform settings: a later step. Suspending a customer should need two people's approval, which is not built." },
];
