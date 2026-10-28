"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Bell, CalendarCheck, ChevronDown, CircleHelp, CreditCard, LogOut, Menu, Search, Settings,
  ShieldAlert, UserPlus, UserRound, Wallet, type LucideIcon,
} from "lucide-react";
import BrandLoader from "@/components/ui/BrandLoader";

type OpenMenu = "notifications" | "profile" | null;

type Notification = { id: number; icon: LucideIcon; tone: string; title: string; body: string; time: string; unread: boolean };

const initialNotifications: Notification[] = [
  { id: 1, icon: UserPlus, tone: "bg-emerald-500", title: "New registration", body: "Aarav Sharma joined the Family Care plan.", time: "2 min ago", unread: true },
  { id: 2, icon: Wallet, tone: "bg-brand", title: "Payment received", body: "$1,240.00 from Northwell Health.", time: "18 min ago", unread: true },
  { id: 3, icon: ShieldAlert, tone: "bg-rose-500", title: "Failed sign-in attempts", body: "5 failed logins for d.kim@carepoint.com.", time: "1 hr ago", unread: true },
  { id: 4, icon: CalendarCheck, tone: "bg-sky", title: "Appointments milestone", body: "8,900 appointments booked this month.", time: "3 hr ago", unread: false },
  { id: 5, icon: CreditCard, tone: "bg-navy", title: "Refund requested", body: "Rohan Patel requested a refund of $49.", time: "Yesterday", unread: false },
];

export default function Topbar({ onOpenMobile }: { onOpenMobile: () => void }) {
  const [open, setOpen] = useState<OpenMenu>(null);
  const [notifications, setNotifications] = useState(initialNotifications);
  const [signingOut, setSigningOut] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const unread = notifications.filter((n) => n.unread).length;

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(null); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(null); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const toggle = (m: Exclude<OpenMenu, null>) => setOpen((o) => (o === m ? null : m));
  const markRead = (id?: number) =>
    setNotifications((ns) => ns.map((n) => (id === undefined || n.id === id ? { ...n, unread: false } : n)));

  function signOut() {
    setOpen(null);
    setSigningOut(true);
    setTimeout(() => router.push("/login"), 900);
  }

  return (
    <header className="sticky top-0 z-30 flex items-center gap-4 bg-neu/80 px-4 py-4 backdrop-blur-md sm:px-8">
      {signingOut && <BrandLoader label="Signing out" />}
      <button onClick={onOpenMobile} aria-label="Open menu" className="neu-btn grid size-11 place-items-center rounded-xl text-navy lg:hidden">
        <Menu className="size-5" />
      </button>

      <label className="group relative hidden max-w-md flex-1 sm:block">
        <Search className="pointer-events-none absolute left-4 top-1/2 size-4.5 -translate-y-1/2 text-slate-400 transition group-focus-within:text-brand" />
        <input placeholder="Search users, plans, payments…"
          className="neu-inset w-full rounded-2xl py-3 pl-11 pr-16 text-sm text-navy-900 outline-none transition placeholder:text-slate-400 focus:ring-2 focus:ring-brand/30" />
        <kbd className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md bg-neu px-1.5 py-0.5 text-[11px] font-semibold text-slate-400 shadow-neu-sm">⌘K</kbd>
      </label>

      <div ref={ref} className="ml-auto flex items-center gap-3 sm:relative">
        <button aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`} aria-expanded={open === "notifications"}
          onClick={() => toggle("notifications")}
          className={`neu-btn group relative grid size-11 place-items-center rounded-xl text-navy ${open === "notifications" ? "!shadow-neu-in-sm text-brand" : ""}`}>
          <Bell className="size-5 group-hover:animate-[wiggle_0.5s_ease-in-out]" />
          {unread > 0 && (
            <>
              <span className="absolute -right-1 -top-1 grid min-w-5 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold leading-5 text-white ring-2 ring-neu">{unread}</span>
              <span className="absolute -right-1 -top-1 size-5 animate-ping rounded-full bg-rose-400/60" />
            </>
          )}
        </button>

        <button aria-expanded={open === "profile"} onClick={() => toggle("profile")}
          className={`neu-btn flex items-center gap-3 rounded-2xl py-1.5 pl-1.5 pr-3 ${open === "profile" ? "!shadow-neu-in-sm" : ""}`}>
          <span className="grid size-9 place-items-center rounded-xl bg-gradient-to-br from-brand to-sky text-sm font-bold text-white">AD</span>
          <span className="hidden text-left leading-tight sm:block">
            <span className="block text-sm font-semibold text-navy-900">Admin</span>
            <span className="block text-xs text-slate-500">Super Admin</span>
          </span>
          <ChevronDown className={`hidden size-4 text-slate-400 transition-transform duration-300 sm:block ${open === "profile" ? "rotate-180 text-brand" : ""}`} />
        </button>

        {open === "notifications" && (
          <Panel className="sm:w-[380px]">
            <div className="flex items-center justify-between px-5 pb-3 pt-4">
              <div>
                <h3 className="font-semibold text-navy-900">Notifications</h3>
                <p className="text-xs text-slate-500">{unread ? `You have ${unread} unread` : "You're all caught up"}</p>
              </div>
              {unread > 0 && (
                <button onClick={() => markRead()} className="text-xs font-semibold text-brand transition hover:underline">Mark all as read</button>
              )}
            </div>
            <ul className="scrollbar-thin max-h-[min(60vh,360px)] space-y-1 overflow-y-auto px-2">
              {notifications.map((n, i) => {
                const Icon = n.icon;
                return (
                  <li key={n.id} className="animate-fade-up" style={{ animationDelay: `${i * 0.04}s`, animationDuration: "0.35s" }}>
                    <button onClick={() => markRead(n.id)}
                      className={`group flex w-full items-start gap-3 rounded-2xl p-3 text-left transition duration-300 hover:bg-white/70 hover:shadow-neu-sm ${n.unread ? "bg-white/40" : ""}`}>
                      <span className={`grid size-9 shrink-0 place-items-center rounded-xl text-white transition group-hover:scale-110 ${n.tone}`}>
                        <Icon className="size-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className={`truncate text-sm ${n.unread ? "font-semibold text-navy-900" : "font-medium text-navy-700"}`}>{n.title}</span>
                          {n.unread && <span className="size-2 shrink-0 rounded-full bg-brand" aria-label="Unread" />}
                        </span>
                        <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">{n.body}</span>
                        <span className="mt-1 block text-[11px] font-medium text-slate-400">{n.time}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <div className="mt-2 border-t border-neu-dark/60 p-2">
              <Link href="/dashboard/notifications" onClick={() => setOpen(null)}
                className="block rounded-xl py-2.5 text-center text-sm font-semibold text-brand transition hover:bg-white/70 hover:shadow-neu-sm">
                View all notifications
              </Link>
            </div>
          </Panel>
        )}

        {open === "profile" && (
          <Panel className="sm:w-[260px]">
            <div className="flex items-center gap-3 px-4 pb-3 pt-4">
              <span className="grid size-11 place-items-center rounded-xl bg-gradient-to-br from-brand to-sky font-bold text-white shadow-glow">AD</span>
              <div className="min-w-0 leading-tight">
                <p className="truncate font-semibold text-navy-900">Admin</p>
                <p className="truncate text-xs text-slate-500">admin@zoikoaccess.com</p>
              </div>
            </div>
            <ul className="space-y-0.5 border-t border-neu-dark/60 p-2">
              <MenuLink href="/dashboard/profile" icon={UserRound} onClick={() => setOpen(null)}>My Profile</MenuLink>
              <MenuLink href="/dashboard/settings" icon={Settings} onClick={() => setOpen(null)}>Account Settings</MenuLink>
              <MenuLink href="/dashboard/payments/invoices" icon={CreditCard} onClick={() => setOpen(null)}>Billing</MenuLink>
              <MenuLink href="/dashboard/faq" icon={CircleHelp} onClick={() => setOpen(null)}>Help Center</MenuLink>
            </ul>
            <div className="border-t border-neu-dark/60 p-2">
              <button onClick={signOut}
                className="group flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-rose-500 transition hover:bg-rose-50 hover:shadow-neu-sm">
                <LogOut className="size-4 transition-transform group-hover:translate-x-1" /> Log out
              </button>
            </div>
          </Panel>
        )}
      </div>
    </header>
  );
}

/** Dropdown surface: full-width sheet under the top bar on phones, anchored to the right on larger screens. */
function Panel({ className = "", children }: { className?: string; children: ReactNode }) {
  return (
    <div role="menu"
      className={`absolute inset-x-4 top-full z-40 mt-2 origin-top-right overflow-hidden rounded-3xl bg-neu shadow-neu-lg ring-1 ring-white/70 animate-[pop_0.25s_cubic-bezier(0.22,1,0.36,1)_both] sm:inset-x-auto sm:right-0 sm:mt-3 ${className}`}>
      {children}
    </div>
  );
}

function MenuLink({ href, icon: Icon, onClick, children }: { href: string; icon: LucideIcon; onClick: () => void; children: ReactNode }) {
  return (
    <li>
      <Link href={href} onClick={onClick} role="menuitem"
        className="group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-navy-700 transition duration-300 hover:bg-white/70 hover:text-brand hover:shadow-neu-sm">
        <Icon className="size-4 text-slate-400 transition group-hover:scale-110 group-hover:text-brand" />
        <span className="transition-transform group-hover:translate-x-0.5">{children}</span>
      </Link>
    </li>
  );
}
