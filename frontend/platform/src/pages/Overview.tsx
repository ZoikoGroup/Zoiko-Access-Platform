import type { Health, Overview as OverviewData } from "../types";
import { Card, Icon, Pill, StatCard } from "../ui";
import { CustomersTable } from "./Customers";

interface Concern { kind: "bad" | "warn"; text: string }

function concerns(d: OverviewData): Concern[] {
  const out: Concern[] = [];
  for (const t of d.tenants) {
    if (!t.audit.chain_ok) out.push({ kind: "bad", text: `${t.name}: audit log tampering detected at entry #${t.audit.chain_bad_seq}` });
    if (t.status !== "active") out.push({ kind: "warn", text: `${t.name} is ${t.status}` });
    if (t.audit.failed_logins >= 5) out.push({ kind: "warn", text: `${t.name}: ${t.audit.failed_logins} failed sign-ins in 24 hours` });
  }
  if (d.outbox.quarantined > 0) out.push({ kind: "bad", text: `${d.outbox.quarantined} event(s) gave up delivering and need a person` });
  if (d.mail.failed > 0) out.push({ kind: "bad", text: `${d.mail.failed} email(s) could not be sent and gave up` });
  if ((d.outbox.oldest_pending_seconds ?? 0) > 300) {
    out.push({ kind: "warn", text: `Oldest waiting event is ${Math.round((d.outbox.oldest_pending_seconds ?? 0) / 60)} minutes old` });
  }
  return out;
}

export function OverviewPage({ data, api, database, onRefresh, busy }: {
  data: OverviewData; api: Health; database: Health; onRefresh: () => void; busy: boolean;
}) {
  const customers = data.tenants.filter((t) => t.type !== "platform");
  const sum = (pick: (t: OverviewData["tenants"][number]) => number) => customers.reduce((n, t) => n + pick(t), 0);
  const issues = concerns(data);
  return (
    <>
      <div className="hero">
        <div>
          <h1>Platform overview</h1>
          <p>The health of every customer in cell {data.cell}. Numbers only: this console never shows a patient's information.</p>
        </div>
        <span className="spacer" />
        <span className="mono" title="When these numbers were read">{new Date(data.generated_at).toLocaleTimeString()}</span>
        <button className="btn" onClick={onRefresh} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button>
      </div>

      <div className="grid stats">
        <StatCard icon="organizations" label="Customers" value={customers.length} note="organizations on the platform" />
        <StatCard icon="patients" label="Patients" value={sum((t) => t.counts.patients)} note="across all customers" />
        <StatCard icon="appointments" label="Bookings waiting" value={sum((t) => t.bookings.requested)} note="not yet confirmed" />
        <StatCard icon="bolt" label="Events waiting" value={data.outbox.pending} note={`${data.outbox.quarantined} stuck`} />
        <StatCard icon="shield" label="Needs attention" value={issues.length} note={issues.length ? "see the list below" : "all clear"} />
      </div>

      <div className="grid cols-2">
        <Card title="Needs attention" sub="Things a person should look at">
          {issues.length === 0 ? (
            <div className="empty">Nothing needs attention: every audit log is intact and no events are stuck.</div>
          ) : (
            <ul className="checklist">
              {issues.map((c) => <li key={c.text}><Pill kind={c.kind}>{c.kind === "bad" ? "urgent" : "check"}</Pill> {c.text}</li>)}
            </ul>
          )}
        </Card>
        <Card title="System health" sub="This cell right now">
          <ul className="checklist">
            <li><Icon name="check" size={16} /> API <span className="right"><i className={`dot ${api}`} />{api}</span></li>
            <li><Icon name="check" size={16} /> Database <span className="right"><i className={`dot ${database}`} />{database}</span></li>
            <li><Icon name="check" size={16} /> Events waiting to be delivered <span className="right">{data.outbox.pending}</span></li>
            <li><Icon name="check" size={16} /> Events that gave up <span className="right">{data.outbox.quarantined}</span></li>
            <li><Icon name="check" size={16} /> Emails waiting to be sent <span className="right">{data.mail.queued}</span></li>
            <li><Icon name="check" size={16} /> Emails that gave up <span className="right">{data.mail.failed}</span></li>
            <li><Icon name="check" size={16} /> Oldest waiting event <span className="right">{data.outbox.oldest_pending_seconds === null ? "none" : `${data.outbox.oldest_pending_seconds}s`}</span></li>
          </ul>
        </Card>
      </div>

      <div className="grid">
        <Card title="Customers" sub="One row each, aggregate numbers only">
          <CustomersTable rows={data.tenants} detailed={false} />
        </Card>
      </div>
    </>
  );
}
