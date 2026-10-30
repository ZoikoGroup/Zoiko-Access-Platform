import type { TenantRow } from "../types";
import { Pill, Table, timeOf } from "../ui";

function chain(t: TenantRow) {
  return t.audit.chain_ok
    ? <Pill kind="ok">intact</Pill>
    : <Pill kind="bad">broken at #{t.audit.chain_bad_seq}</Pill>;
}

function lastSeen(t: TenantRow): string {
  return t.audit.last_event_at ? `${t.audit.last_event_at.slice(0, 10)} ${timeOf(t.audit.last_event_at)}` : "never";
}

/** One row per customer. Aggregate numbers only. */
export function CustomersTable({ rows, detailed }: { rows: TenantRow[]; detailed: boolean }) {
  const headers = detailed
    ? ["Customer", "Type", "Status", "Self sign-up", "Patients", "Staff", "Sites", "Services",
       "Bookings (new / confirmed / cancelled)", "Free slots", "Checks (24h)", "Denied (24h)",
       "Failed sign-ins (24h)", "Audit chain", "Last activity"]
    : ["Customer", "Status", "Patients", "Bookings waiting", "Failed sign-ins (24h)", "Audit chain"];
  const rowsOut = rows.map((t) => {
    const status = <Pill key="s" kind={t.status === "active" ? "ok" : "bad"}>{t.status}</Pill>;
    return detailed
      ? [t.name, t.type.replace("_", " "), status, t.self_registration ? "open" : "closed",
         t.counts.patients, t.counts.staff, t.counts.organizations,
         `${t.services.active}${t.services.inactive ? ` (+${t.services.inactive} hidden)` : ""}`,
         `${t.bookings.requested} / ${t.bookings.confirmed} / ${t.bookings.cancelled}`,
         t.bookings.open_slots, t.decisions.total, t.decisions.denied, t.audit.failed_logins,
         <span key="c">{chain(t)}</span>, lastSeen(t)]
      : [t.name, status, t.counts.patients, t.bookings.requested, t.audit.failed_logins,
         <span key="c">{chain(t)}</span>];
  });
  return <Table headers={headers} rows={rowsOut} empty="No customers yet." />;
}
