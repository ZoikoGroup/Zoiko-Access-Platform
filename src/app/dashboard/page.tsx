import type { Metadata } from "next";
import { CalendarCheck, CalendarDays, Crown, Download, DollarSign, Ellipsis, Users } from "lucide-react";
import StatCard from "@/components/dashboard/StatCard";
import OrderValueChart from "@/components/dashboard/OrderValueChart";

export const metadata: Metadata = { title: "Dashboard · Zoiko Access Admin" };

const stats = [
  { title: "Active Users", value: 4200, change: 15, icon: Users,
    tone: { text: "text-emerald-600", icon: "bg-emerald-500", ring: "ring-emerald-300/50" } },
  { title: "Total Subscription", value: 24307, change: 10, icon: Crown,
    tone: { text: "text-brand", icon: "bg-brand", ring: "ring-brand/20" } },
  { title: "Appointments Booked", value: 8932, change: 8, icon: CalendarCheck,
    tone: { text: "text-sky", icon: "bg-sky", ring: "ring-sky/20" } },
  { title: "Monthly Revenue", value: 1.02, prefix: "$", suffix: "M", decimals: 2, change: 12, icon: DollarSign,
    tone: { text: "text-navy", icon: "bg-navy", ring: "ring-navy/15" } },
];

const integrations = [
  { name: "Google Drive", value: 2840 },
  { name: "Meta Ads", value: 2310 },
  { name: "Notion", value: 1920 },
  { name: "Dropbox", value: 1460 },
  { name: "Slackbot", value: 980 },
];

const plans = [
  { name: "Family Care", users: 9840 },
  { name: "Individual Plus", users: 7215 },
  { name: "Organisation", users: 4630 },
  { name: "Basic", users: 2622 },
];

const users = [
  { name: "Aarav Sharma", email: "aarav@medicore.in", plan: "Family Care", date: "Oct 28, 2026", status: "Active" },
  { name: "Emily Carter", email: "emily.c@northwell.org", plan: "Organisation", date: "Oct 27, 2026", status: "Active" },
  { name: "Rohan Patel", email: "rohan.p@gmail.com", plan: "Individual Plus", date: "Oct 27, 2026", status: "Pending" },
  { name: "Sofia Martinez", email: "sofia@healthbridge.io", plan: "Basic", date: "Oct 26, 2026", status: "Active" },
  { name: "Daniel Kim", email: "d.kim@carepoint.com", plan: "Family Care", date: "Oct 25, 2026", status: "Suspended" },
];

const statusStyle: Record<string, string> = {
  Active: "bg-emerald-50 text-emerald-700",
  Pending: "bg-amber-50 text-amber-700",
  Suspended: "bg-rose-50 text-rose-700",
};

export default function DashboardPage() {
  const maxIntegration = 3000; // matches the 0K–3K axis below the bars
  const totalPlans = plans.reduce((s, p) => s + p.users, 0);

  return (
    <div className="mx-auto max-w-[1500px]">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-neu-dark/70 pb-6 pt-2 animate-fade-up">
        <div>
          <h1 className="text-4xl font-bold tracking-tight text-navy-900 sm:text-5xl">Dashboard</h1>
          <p className="mt-2 text-slate-500">Welcome back — here&apos;s what&apos;s happening across Zoiko Access today.</p>
        </div>
        <div className="flex gap-3">
          <button className="neu-btn flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-navy">
            <CalendarDays className="size-4" /> Oct 1 – Oct 28
          </button>
          <button className="shine flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand to-[#7d4cf5] px-4 py-2.5 text-sm font-semibold text-white shadow-glow transition hover:-translate-y-0.5">
            <Download className="size-4" /> Export
          </button>
        </div>
      </div>

      <section className="mt-8 grid gap-6 sm:grid-cols-2 2xl:grid-cols-4">
        {stats.map((s, i) => <StatCard key={s.title} {...s} delay={0.05 + i * 0.07} />)}
      </section>

      <section className="mt-8 grid gap-6 xl:grid-cols-3">
        <OrderValueChart />

        <article className="neu-card neu-hover p-6 animate-fade-up [animation-delay:0.35s] sm:p-7">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-navy-900">Top 5 Integrations</h3>
            <button aria-label="More" className="neu-btn grid size-9 place-items-center rounded-lg text-slate-500"><Ellipsis className="size-4" /></button>
          </div>
          <p className="mt-1 text-sm text-slate-500">Connected accounts this month</p>
          <ul className="mt-6 space-y-5">
            {integrations.map((it, i) => (
              <li key={it.name} className="group" title={`${it.name}: ${it.value.toLocaleString()} connections`}>
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="font-medium text-navy-700 transition group-hover:translate-x-1 group-hover:text-brand">{it.name}</span>
                  <span className="font-semibold tabular-nums text-navy-900">{it.value.toLocaleString()}</span>
                </div>
                <div className="h-2.5 rounded-full bg-neu shadow-neu-in-sm">
                  <div className="h-full origin-left rounded-full bg-gradient-to-r from-brand to-sky transition-[filter] animate-[grow-x_1s_cubic-bezier(0.22,1,0.36,1)_both] group-hover:brightness-110"
                    style={{ width: `${(it.value / maxIntegration) * 100}%`, animationDelay: `${0.5 + i * 0.1}s` }} />
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-6 flex justify-between text-xs font-medium text-slate-400">
            <span>0K</span><span>1K</span><span>2K</span><span>3K</span>
          </div>
        </article>
      </section>

      <section className="mt-8 grid gap-6 xl:grid-cols-3">
        <article className="neu-card min-w-0 p-6 animate-fade-up [animation-delay:0.45s] sm:p-7 xl:col-span-2">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-navy-900">Recent Registrations</h3>
            <a href="/dashboard/user-plan/users" className="text-sm font-semibold text-brand transition hover:underline">View all</a>
          </div>
          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-slate-400">
                <tr><th className="pb-3 font-semibold">User</th><th className="pb-3 font-semibold">Plan</th><th className="pb-3 font-semibold">Joined</th><th className="pb-3 font-semibold">Status</th></tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.email} className="group transition hover:bg-white/50">
                    <td className="rounded-l-2xl py-3 pl-2">
                      <div className="flex items-center gap-3">
                        <span className="grid size-10 place-items-center rounded-xl bg-neu text-xs font-bold text-brand shadow-neu-sm transition group-hover:scale-110">
                          {u.name.split(" ").map((p) => p[0]).join("")}
                        </span>
                        <span className="leading-tight">
                          <span className="block font-semibold text-navy-900">{u.name}</span>
                          <span className="block text-xs text-slate-500">{u.email}</span>
                        </span>
                      </div>
                    </td>
                    <td className="py-3 text-navy-700">{u.plan}</td>
                    <td className="py-3 text-slate-500">{u.date}</td>
                    <td className="rounded-r-2xl py-3">
                      <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${statusStyle[u.status]}`}>{u.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="neu-card neu-hover p-6 animate-fade-up [animation-delay:0.55s] sm:p-7">
          <h3 className="text-lg font-semibold text-navy-900">Plan Distribution</h3>
          <p className="mt-1 text-sm text-slate-500">{totalPlans.toLocaleString()} subscribers</p>
          <ul className="mt-6 space-y-4">
            {plans.map((p, i) => {
              const pct = Math.round((p.users / totalPlans) * 100);
              return (
                <li key={p.name} className="group neu-inset rounded-2xl p-4 transition hover:shadow-neu-sm" title={`${p.name}: ${p.users.toLocaleString()} users`}>
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium text-navy-700">{p.name}</span>
                    <span className="font-semibold tabular-nums text-navy-900">{pct}%</span>
                  </div>
                  <div className="mt-2.5 h-1.5 rounded-full bg-neu-dark/50">
                    <div className="h-full origin-left rounded-full bg-brand animate-[grow-x_1s_cubic-bezier(0.22,1,0.36,1)_both]"
                      style={{ width: `${pct}%`, animationDelay: `${0.7 + i * 0.1}s` }} />
                  </div>
                </li>
              );
            })}
          </ul>
        </article>
      </section>
    </div>
  );
}
