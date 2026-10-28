"use client";

import { useState } from "react";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import NavProgress from "./NavProgress";

export default function AdminShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="relative flex min-h-screen bg-neu">
      <NavProgress />
      {/* Soft brand glows behind the neumorphic surface, as in the reference */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-40 right-0 size-[520px] rounded-full bg-brand/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 size-[420px] rounded-full bg-sky/10 blur-3xl" />
      </div>
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)}
        mobileOpen={mobileOpen} onCloseMobile={() => setMobileOpen(false)} />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <Topbar onOpenMobile={() => setMobileOpen(true)} />
        <main className="flex-1 px-4 pb-10 sm:px-8">{children}</main>
      </div>
    </div>
  );
}
