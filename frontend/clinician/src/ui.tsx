// Small shared building blocks. No chart library: the donut is plain SVG.
import type { ReactNode } from "react";

const ICONS: Record<string, string> = {
  dashboard: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  patients: "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0z",
  providers: "M6 3v7a4 4 0 008 0V3M10 14v3a4 4 0 008 0v-2M18 11a2 2 0 100 4 2 2 0 000-4z",
  organizations: "M4 21V5l8-2v18M12 9h8v12M7 8h2M7 12h2M7 16h2M15 13h2M15 17h2",
  services: "M5 4h14v16H5zM9 9h6M9 13h6M9 17h3",
  appointments: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  referrals: "M4 12h13M13 6l6 6-6 6",
  workflows: "M4 6h6v4H4zM14 14h6v4h-6zM10 8h4v8h0M7 10v4h7",
  messages: "M3 6h18v12H3zM3 7l9 7 9-7",
  billing: "M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2zM9 8h6M9 12h6",
  compliance: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4",
  reports: "M5 20V10M11 20V4M17 20v-7",
  integrations: "M9 3v5M15 3v5M6 8h12v4a6 6 0 01-12 0zM12 18v3",
  settings: "M12 9a3 3 0 100 6 3 3 0 000-6zM4 12h2M18 12h2M12 4v2M12 18v2M6.5 6.5L8 8M16 16l1.5 1.5M6.5 17.5L8 16M16 8l1.5-1.5",
  search: "M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4",
  check: "M4 12l5 5L20 6",
  link: "M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3A4 4 0 0011 18.7l1-1",
  bolt: "M13 3L5 14h6l-1 7 8-11h-6z",
};

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name] ?? ICONS.dashboard} />
    </svg>
  );
}

export function Logo({ size = 38 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <rect width="40" height="40" rx="10" fill="#1f5fe0" />
      <path d="M11 12h18L15 28h14" fill="none" stroke="#fff" strokeWidth="3.4"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Card({ title, sub, action, children, className = "" }: {
  title?: ReactNode; sub?: ReactNode; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <div className="head">
          <h2>{title}</h2>
          {action}
        </div>
      )}
      {sub && <div className="sub">{sub}</div>}
      {children}
    </section>
  );
}

export function StatCard({ icon, label, value, note }: {
  icon: string; label: string; value: ReactNode; note?: string;
}) {
  return (
    <section className="card stat">
      <div className="icon"><Icon name={icon} size={22} /></div>
      <div>
        <div className="label">{label}</div>
        <div className="value">{value}</div>
        {note && <div className="note">{note}</div>}
      </div>
    </section>
  );
}

export type PillKind = "ok" | "bad" | "warn" | "info";

export function Pill({ kind, children }: { kind: PillKind; children: ReactNode }) {
  return <span className={`pill ${kind}`}>{children}</span>;
}

export function Table({ headers, rows, empty }: {
  headers: string[]; rows: ReactNode[][]; empty: string;
}) {
  if (rows.length === 0) return <div className="empty">{empty}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((cells, r) => (
            <tr key={r}>{cells.map((cell, c) => <td key={c}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export interface Slice { label: string; value: number; color: string }

export function Donut({ slices, centerLabel }: { slices: Slice[]; centerLabel: string }) {
  const total = slices.reduce((sum, s) => sum + s.value, 0);
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg width="150" height="150" viewBox="0 0 150 150" role="img"
        aria-label={`${centerLabel}: ${slices.map((s) => `${s.label} ${s.value}`).join(", ")}`}>
        <circle cx="75" cy="75" r={radius} fill="none" stroke="var(--line)" strokeWidth="18" />
        {total > 0 && slices.filter((s) => s.value > 0).map((s) => {
          const length = (s.value / total) * circumference;
          const node = (
            <circle key={s.label} cx="75" cy="75" r={radius} fill="none" stroke={s.color}
              strokeWidth="18" strokeDasharray={`${length} ${circumference - length}`}
              strokeDashoffset={-offset} transform="rotate(-90 75 75)" />
          );
          offset += length;
          return node;
        })}
        <text x="75" y="73" textAnchor="middle" fontSize="24" fontWeight="700" fill="var(--ink)">{total}</text>
        <text x="75" y="93" textAnchor="middle" fontSize="11" fill="var(--muted)">{centerLabel}</text>
      </svg>
      <div className="legend">
        {slices.map((s) => (
          <div key={s.label}><i style={{ background: s.color }} />{s.label} <b>{s.value}</b>
            {total > 0 && <span style={{ color: "var(--muted)" }}> ({Math.round((s.value / total) * 100)}%)</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export const shortId = (id: string): string => (id ? `${id.slice(0, 8)}…` : "");
export const timeOf = (iso: string): string => iso.slice(11, 19);
