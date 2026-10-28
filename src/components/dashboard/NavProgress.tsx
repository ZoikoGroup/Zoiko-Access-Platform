"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

/** Thin gradient bar across the top of the page while an internal link navigates. */
export default function NavProgress() {
  const pathname = usePathname();
  const [active, setActive] = useState(false);

  useEffect(() => setActive(false), [pathname]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      const a = (e.target as HTMLElement).closest("a");
      const href = a?.getAttribute("href");
      if (!href?.startsWith("/") || href === window.location.pathname) return;
      setActive(true);
    }
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  if (!active) return null;
  return (
    <div className="fixed inset-x-0 top-0 z-[90] h-[3px]">
      <div className="h-full animate-progress rounded-r-full bg-gradient-to-r from-brand via-[#9b6bff] to-sky shadow-[0_0_12px_#6a3cf0]" />
    </div>
  );
}
