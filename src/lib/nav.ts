import {
  LayoutGrid, UserSquare, Wallet, Blocks, Sparkles, Mail, BadgeDollarSign, Megaphone, CircleHelp,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  children?: { label: string; href: string }[];
};

export const navItems: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutGrid },
  {
    label: "User Plan", href: "/dashboard/user-plan", icon: UserSquare,
    children: [
      { label: "All Users", href: "/dashboard/user-plan/users" },
      { label: "Active Plans", href: "/dashboard/user-plan/active" },
      { label: "Subscriptions", href: "/dashboard/user-plan/subscriptions" },
    ],
  },
  {
    label: "Payments", href: "/dashboard/payments", icon: Wallet,
    children: [
      { label: "Transactions", href: "/dashboard/payments/transactions" },
      { label: "Invoices", href: "/dashboard/payments/invoices" },
      { label: "Refunds", href: "/dashboard/payments/refunds" },
    ],
  },
  { label: "Tools Integrations", href: "/dashboard/integrations", icon: Blocks },
  { label: "AI API Settings", href: "/dashboard/ai-api", icon: Sparkles },
  {
    label: "Email Settings", href: "/dashboard/email", icon: Mail,
    children: [
      { label: "Templates", href: "/dashboard/email/templates" },
      { label: "SMTP Configuration", href: "/dashboard/email/smtp" },
    ],
  },
  { label: "Plan Settings", href: "/dashboard/plan-settings", icon: BadgeDollarSign },
  { label: "Announcements", href: "/dashboard/announcements", icon: Megaphone },
  { label: "FAQ Settings", href: "/dashboard/faq", icon: CircleHelp },
];

/** Flattened lookup used for page titles and breadcrumbs. */
export function findNavLabel(pathname: string): string | undefined {
  for (const item of navItems) {
    const child = item.children?.find((c) => c.href === pathname);
    if (child) return child.label;
    if (item.href === pathname) return item.label;
  }
}
