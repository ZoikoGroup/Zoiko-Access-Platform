"use client";

import { flushSync } from "react-dom";
import { Moon, Sun } from "lucide-react";

type Props = { variant?: "neu" | "glass"; className?: string };

/** Light/dark switch. The new theme is revealed as a circle expanding from the button. */
export default function ThemeToggle({ variant = "neu", className = "" }: Props) {
  function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    const root = document.documentElement;
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    const apply = () => {
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch {}
    };

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!document.startViewTransition || reduce) return apply();

    const { left, top, width, height } = e.currentTarget.getBoundingClientRect();
    const x = left + width / 2;
    const y = top + height / 2;
    const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));

    document.startViewTransition(() => flushSync(apply)).ready.then(() => {
      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
        { duration: 650, easing: "cubic-bezier(0.22, 1, 0.36, 1)", pseudoElement: "::view-transition-new(root)" },
      );
    });
  }

  const look =
    variant === "glass"
      ? "border border-white/25 bg-white/15 text-white backdrop-blur-md hover:bg-white/25 lg:border-slate-200 lg:bg-card lg:text-navy-900 lg:shadow-sm lg:hover:bg-card lg:hover:text-brand"
      : "neu-btn text-navy";

  return (
    <button type="button" onClick={toggle} aria-label="Toggle dark mode" title="Toggle dark mode"
      className={`group relative grid size-11 place-items-center overflow-hidden rounded-xl transition ${look} ${className}`}>
      <Sun className="absolute size-5 rotate-0 scale-100 transition duration-500 group-hover:rotate-45 dark:-rotate-90 dark:scale-0 dark:opacity-0" />
      <Moon className="absolute size-5 rotate-90 scale-0 opacity-0 transition duration-500 group-hover:-rotate-12 dark:rotate-0 dark:scale-100 dark:opacity-100" />
    </button>
  );
}
