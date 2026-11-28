// The platform console menu. `live` screens show real backend data; the rest say what they wait for.
export type PageId = "overview" | "customers" | "partnerchecks" | "purposes" | "incidents" | "hazards" | "releases" | "packs" | "connectors" | "codemaps" | "attempts" | "egress" | "aipermits" | "templates" | "support" | "levels" | "readiness" | "matrix" | "settings" | "account";

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
  { id: "partnerchecks", label: "Partner checks", icon: "integrations", live: true },
  { id: "purposes", label: "Intended purposes", icon: "shield", live: true },
  { id: "incidents", label: "Incidents", icon: "shield", live: true },
  { id: "hazards", label: "Hazards", icon: "shield", live: true },
  { id: "releases", label: "Releases", icon: "integrations", live: true },
  { id: "matrix", label: "Requirements matrix", icon: "reports", live: true },
  { id: "readiness", label: "Go-live readiness", icon: "shield", live: true },
  { id: "levels", label: "Service levels", icon: "dashboard", live: true },
  { id: "packs", label: "Country packs", icon: "compliance", live: true },
  { id: "connectors", label: "Partner systems", icon: "integrations", live: true },
  { id: "codemaps", label: "Code mapping", icon: "integrations", live: true },
  { id: "attempts", label: "Delivery attempts", icon: "integrations", live: true },
  { id: "egress", label: "Outgoing addresses", icon: "integrations", live: true },
  { id: "aipermits", label: "AI permits", icon: "integrations", live: true },
  { id: "templates", label: "Message templates", icon: "messages", live: true },
  { id: "support", label: "Support access", icon: "shield", live: true },
  { id: "settings", label: "Settings", icon: "settings", live: true },
  { id: "account", label: "My account", icon: "settings", live: true },
];
