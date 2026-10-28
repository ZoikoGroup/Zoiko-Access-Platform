"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ChevronDown, LogOut, PanelLeft, Settings, X } from "lucide-react";
import { navItems, type NavItem } from "@/lib/nav";
import Logo from "@/components/ui/Logo";
import BrandLoader from "@/components/ui/BrandLoader";

type Props = {
  collapsed: boolean;
  onToggle: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
};

export default function Sidebar({ collapsed, onToggle, mobileOpen, onCloseMobile }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const [signingOut, setSigningOut] = useState(false);
  const [open, setOpen] = useState<string | null>(
    () => navItems.find((i) => i.children && pathname.startsWith(i.href))?.label ?? null,
  );

  useEffect(onCloseMobile, [pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  function signOut() {
    setSigningOut(true);
    setTimeout(() => router.push("/login"), 900);
  }

  const isActive = (item: NavItem) =>
    item.href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(item.href);

  // Collapsed rail only applies on desktop; the mobile drawer is always full width.
  const mini = collapsed && !mobileOpen;

  return (
    <>
      {signingOut && <BrandLoader label="Signing out" />}
      <div onClick={onCloseMobile}
        className={`fixed inset-0 z-40 bg-navy-900/30 backdrop-blur-sm transition-opacity lg:hidden ${mobileOpen ? "opacity-100" : "pointer-events-none opacity-0"}`} />

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex flex-col bg-neu transition-[width,transform] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] lg:sticky lg:top-0 lg:h-screen lg:translate-x-0
          ${mobileOpen ? "translate-x-0 shadow-neu-lg" : "-translate-x-full"}
          ${mini ? "w-[96px]" : "w-[288px]"} border-r border-white/70`}>
        <div className={`flex items-center px-6 pb-6 pt-7 ${mini ? "justify-center px-0" : "justify-between"}`}>
          {mini ? <ZaMark /> : <Logo className="h-11 w-auto" priority />}
          <button onClick={mobileOpen ? onCloseMobile : onToggle} aria-label="Toggle sidebar"
            className={`neu-btn grid size-10 place-items-center rounded-xl text-navy ${mini ? "absolute -right-5 top-8 size-9" : ""}`}>
            {mobileOpen ? <X className="size-4.5" /> : <PanelLeft className="size-4.5" />}
          </button>
        </div>

        <nav className="scrollbar-thin flex-1 space-y-1.5 overflow-y-auto overflow-x-hidden px-4 py-2">
          {navItems.map((item, i) => {
            const Icon = item.icon;
            const active = isActive(item);
            const expanded = open === item.label && !mini;
            const rowCls = `group relative flex w-full items-center gap-3.5 rounded-2xl px-4 py-3 text-[15px] font-medium transition-all duration-300 ${mini ? "justify-center px-0" : ""}
              ${active ? "gradient-border text-navy-900 shadow-neu-sm" : "border-[1.5px] border-transparent text-slate-500 hover:text-navy-900 hover:shadow-neu-sm active:shadow-neu-in-sm"}`;
            const inner = (
              <>
                <Icon className={`size-5 shrink-0 transition-transform duration-300 group-hover:scale-110 ${active ? "text-brand" : "group-hover:text-brand"}`} />
                {!mini && <span className="flex-1 truncate text-left transition-transform duration-300 group-hover:translate-x-0.5">{item.label}</span>}
                {!mini && item.children && (
                  <ChevronDown className={`size-4 transition-transform duration-300 ${expanded ? "rotate-180 text-brand" : ""}`} />
                )}
                {mini && (
                  <span className="pointer-events-none absolute left-full ml-4 whitespace-nowrap rounded-lg bg-navy-900 px-2.5 py-1.5 text-xs text-white opacity-0 shadow-lg transition group-hover:opacity-100">
                    {item.label}
                  </span>
                )}
              </>
            );

            return (
              <div key={item.label} className="animate-fade-up" style={{ animationDelay: `${i * 0.04}s` }}>
                {item.children ? (
                  <button type="button" className={rowCls}
                    onClick={() => {
                      if (mini) onToggle();
                      setOpen(expanded ? null : item.label);
                    }}>
                    {inner}
                  </button>
                ) : (
                  <Link href={item.href} className={rowCls}>{inner}</Link>
                )}

                {item.children && (
                  <div className={`grid transition-[grid-template-rows] duration-400 ease-out ${expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]"}`}>
                    <ul className="ml-7 overflow-hidden border-l-2 border-neu-dark/70 pl-4">
                      {item.children.map((c) => {
                        const childActive = pathname === c.href;
                        return (
                          <li key={c.href}>
                            <Link href={c.href}
                              className={`relative my-1 block rounded-xl px-3 py-2 text-sm transition-all duration-300 hover:translate-x-1 hover:text-brand
                                ${childActive ? "font-semibold text-brand shadow-neu-in-sm" : "text-slate-500"}`}>
                              <span className={`absolute -left-[21px] top-1/2 size-2 -translate-y-1/2 rounded-full transition ${childActive ? "bg-brand" : "bg-transparent"}`} />
                              {c.label}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <div className={`space-y-1.5 border-t border-neu-dark/60 px-4 py-4 ${mini ? "flex flex-col items-center" : ""}`}>
          <Link href="/dashboard/settings"
            className={`group flex items-center gap-3.5 rounded-2xl px-4 py-3 text-[15px] font-medium text-slate-500 transition hover:text-navy-900 hover:shadow-neu-sm ${mini ? "px-3" : "w-full"}`}>
            <Settings className="size-5 transition-transform duration-500 group-hover:rotate-90 group-hover:text-brand" />
            {!mini && "Settings"}
          </Link>
          <button onClick={signOut}
            className={`group flex items-center gap-3.5 rounded-2xl px-4 py-3 text-[15px] font-medium text-slate-500 transition hover:text-rose-500 hover:shadow-neu-sm ${mini ? "px-3" : "w-full"}`}>
            <LogOut className="size-5 transition-transform group-hover:translate-x-1" />
            {!mini && "Log out"}
          </button>
        </div>
      </aside>
    </>
  );
}

function ZaMark() {
  return (
    <div className="grid size-12 place-items-center rounded-2xl bg-navy shadow-glow">
      <span className="text-lg font-black italic tracking-tighter text-white">
        Z<span className="text-brand-300">A</span>
      </span>
    </div>
  );
}
