// The partner app menu. Which items a person sees depends on the kind of login they hold, because
// the server decides what each role may reach; the menu only hides what would be refused.
export type PageId = "home" | "services" | "team" | "programme" | "plans" | "requests" | "keys" | "webhooks" | "documents" | "account";

export interface NavItem {
  id: PageId;
  label: string;
  icon: string;
  live: boolean;
  waitsFor?: string;
  areas?: string[];        // shown only to these kinds of login (default: all)
}

export const NAV: NavItem[] = [
  { id: "home", label: "Overview", icon: "dashboard", live: true },
  { id: "services", label: "My services", icon: "services", live: true, areas: ["partner"] },
  { id: "requests", label: "Incoming requests", icon: "referrals", live: true, areas: ["partner"] },
  { id: "programme", label: "Programme", icon: "reports", live: true, areas: ["employer"] },
  { id: "plans", label: "Plans and members", icon: "services", live: true, areas: ["payer"] },
  { id: "keys", label: "API keys", icon: "integrations", live: true, areas: ["developer"] },
  { id: "webhooks", label: "Webhooks", icon: "referrals", live: true, areas: ["developer"] },
  { id: "documents", label: "API documents", icon: "reports", live: true, areas: ["developer"] },
  { id: "team", label: "My team", icon: "patients", live: true },
  { id: "account", label: "My account", icon: "settings", live: true },
];

export const navFor = (area: string): NavItem[] => NAV.filter((n) => !n.areas || n.areas.includes(area));
