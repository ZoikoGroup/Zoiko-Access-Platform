import type { LucideIcon } from "lucide-react";
import { TrendingUp } from "lucide-react";
import CountUp from "@/components/ui/CountUp";

type Props = {
  title: string;
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  change: number;
  icon: LucideIcon;
  /** Tailwind classes for the accent (title + icon) */
  tone: { text: string; icon: string; ring: string };
  delay?: number;
};

export default function StatCard({ title, value, prefix, suffix, decimals, change, icon: Icon, tone, delay = 0 }: Props) {
  return (
    <article className={`neu-card neu-hover group relative overflow-hidden p-6 animate-fade-up ring-1 ${tone.ring}`}
      style={{ animationDelay: `${delay}s` }}>
      <span className="pointer-events-none absolute -right-10 -top-10 size-32 rounded-full bg-neu-hi/60 opacity-0 blur-2xl transition duration-500 group-hover:opacity-100" />
      <h3 className={`text-[15px] font-semibold ${tone.text}`}>{title}</h3>
      <div className="mt-4 flex items-center gap-4">
        <span className="grid size-14 shrink-0 place-items-center rounded-full bg-neu shadow-neu-in transition-transform duration-500 group-hover:rotate-[-8deg] group-hover:scale-110">
          <span className={`grid size-9 place-items-center rounded-full text-white ${tone.icon}`}>
            <Icon className="size-4.5" />
          </span>
        </span>
        <p className="text-3xl font-bold tracking-tight text-navy-900 xl:text-[2.1rem]">
          <CountUp value={value} prefix={prefix} suffix={suffix} decimals={decimals} />
        </p>
      </div>
      <p className="mt-4 flex items-center gap-1.5 text-sm text-slate-500">
        <TrendingUp className="size-4 text-emerald-500" />
        <span className="font-semibold text-emerald-600">{change}%</span> <span className="truncate">vs last 30 days</span>
      </p>
    </article>
  );
}
