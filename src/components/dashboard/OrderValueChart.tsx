"use client";

import { useMemo, useState } from "react";
import { ArrowUp, ChevronDown } from "lucide-react";
import CountUp from "@/components/ui/CountUp";

type Range = "This Month" | "Last Month" | "This Year";
const ranges: Range[] = ["This Month", "Last Month", "This Year"];
const MAX = 1500;
const ticks = [1500, 1000, 500, 0];

// Deterministic demo data so server and client renders match.
function series(range: Range) {
  const seed = range === "This Month" ? 3 : range === "Last Month" ? 7 : 11;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const count = range === "This Year" ? 10 : range === "This Month" ? 28 : 30;
  return Array.from({ length: count }, (_, i) => {
    const wave = Math.sin((i + seed) * 1.7) * 0.5 + Math.cos((i * seed) % 13) * 0.25;
    const ramp = Math.min(1, (i + 2) / 6);
    const value = Math.round((850 + wave * 520) * (0.45 + 0.55 * ramp));
    const label = range === "This Year" ? `${months[i]} 2026` : `${range === "This Month" ? "Oct" : "Sep"} ${String(i + 1).padStart(2, "0")}`;
    return { label, value: Math.min(value, 1480) };
  });
}

const fmt = (v: number) => (v >= 1000 ? `$${(v / 1000).toFixed(1)}K` : `$${v}`);

export default function OrderValueChart() {
  const [range, setRange] = useState<Range>("This Month");
  const [menuOpen, setMenuOpen] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const data = useMemo(() => series(range), [range]);
  const avg = Math.round(data.reduce((s, d) => s + d.value, 0) / data.length);
  const orders = range === "This Year" ? 11240 : range === "This Month" ? 1020 : 964;

  return (
    <article className="neu-card neu-hover min-w-0 p-6 animate-fade-up [animation-delay:0.25s] sm:p-7 xl:col-span-2">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-navy-900">Average Order Value</h3>
          <p className="mt-3 text-5xl font-bold tracking-tight text-gradient">
            <CountUp key={range} value={avg} prefix="$" />
          </p>
          <p className="mt-2 text-lg text-navy-700"><CountUp key={range + "o"} value={orders} /> Orders</p>
        </div>
        <div className="flex flex-col items-end gap-4">
          <div className="relative">
            <button onClick={() => setMenuOpen((o) => !o)}
              className="neu-btn flex items-center gap-2 rounded-xl px-4 py-2 text-sm text-slate-600">
              {range} <ChevronDown className={`size-4 transition ${menuOpen ? "rotate-180" : ""}`} />
            </button>
            {menuOpen && (
              <ul className="absolute right-0 z-20 mt-2 w-40 overflow-hidden rounded-xl bg-neu p-1.5 shadow-neu animate-fade-up">
                {ranges.map((r) => (
                  <li key={r}>
                    <button onClick={() => { setRange(r); setMenuOpen(false); }}
                      className={`w-full rounded-lg px-3 py-2 text-left text-sm transition hover:bg-neu-hi/70 hover:text-brand ${r === range ? "font-semibold text-brand" : "text-slate-600"}`}>
                      {r}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="flex items-center gap-1 text-sm text-slate-500">
            <ArrowUp className="size-4 text-emerald-500" />
            <span className="font-semibold text-emerald-600">15%</span> vs last 30 days
          </p>
        </div>
      </div>

      <div className="mt-6 flex gap-3" role="img" aria-label={`Bar chart of average order value, ${range}`}>
        <div className="flex h-60 flex-col justify-between pb-6 text-right text-xs font-medium text-slate-400">
          {ticks.map((t) => <span key={t} className="-translate-y-1.5">{fmt(t)}</span>)}
        </div>
        <div className="relative flex-1">
          <div className="pointer-events-none absolute inset-x-0 top-0 h-54 flex-col justify-between">
            {ticks.map((t, i) => (
              <span key={t} className="absolute inset-x-0 border-t border-dashed border-neu-dark" style={{ top: `${(i / 3) * 100}%` }} />
            ))}
          </div>
          <div key={range} className="relative flex h-54 items-end gap-[2px] sm:gap-1" onMouseLeave={() => setHover(null)}>
            {data.map((d, i) => (
              <div key={d.label} className="relative flex h-full flex-1 cursor-pointer items-end justify-center"
                onMouseEnter={() => setHover(i)}>
                <div
                  className={`w-full max-w-4 origin-bottom rounded-t-[4px] bg-gradient-to-t from-brand-300/70 to-brand animate-bar-grow transition-[opacity,filter] duration-200
                    ${hover !== null && hover !== i ? "opacity-35" : ""} ${hover === i ? "brightness-110" : ""}`}
                  style={{ height: `${(d.value / MAX) * 100}%`, animationDelay: `${0.3 + i * 0.025}s` }} />
                {hover === i && (
                  <div className="pointer-events-none absolute z-10 -translate-y-2 whitespace-nowrap rounded-xl bg-midnight px-3 py-2 text-xs text-white shadow-xl animate-fade-up [animation-duration:0.2s]"
                    style={{ bottom: `${(d.value / MAX) * 100}%` }}>
                    <span className="block text-white/60">{d.label}</span>
                    <span className="font-semibold">${d.value.toLocaleString()}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="mt-2 flex justify-between text-sm font-medium text-navy-700">
            <span>{data[0].label}</span>
            <span>{data[data.length - 1].label}</span>
          </div>
        </div>
      </div>
    </article>
  );
}
