import { getDashboard } from "../api";
import type { Dashboard as DashboardData, Health } from "../types";
import { Gate, useLoad } from "../useLoad";
import { Card, Donut, Icon, Pill, StatCard, Table, timeOf } from "../ui";

const PLANNED = [
  { title: "Appointments", note: "Open the Appointments screen for live bookings." },
  { title: "Referrals", note: "Arrives with the workflow engine (B8)." },
  { title: "Claims processed", note: "A later commercial phase." },
  { title: "Patients by region", note: "Needs more than one cell; not planned yet." },
];

export function Dashboard({ tenantName, api, database }: { tenantName: string; api: Health; database: Health }) {
  const result = useLoad(getDashboard);
  return (
    <Gate result={result}>
      {(d) => <View d={d} tenantName={tenantName} api={api} database={database} />}
    </Gate>
  );
}

function View({ d, tenantName, api, database }: { d: DashboardData; tenantName: string; api: Health; database: Health }) {
  const allowed = d.decisions.filter((x) => x.allowed).length;
  const denied = d.decisions.length - allowed;
  const chain = d.chain;
  return (
    <>
      <div className="hero">
        <div>
          <h1>{tenantName}</h1>
          <p>What is happening in this organization right now. Every number is read live from the backend, as you.</p>
        </div>
        <span className="spacer" />
        <div className="mono" title="Your computer's clock">{new Date().toLocaleString()}</div>
      </div>

      <div className="grid stats">
        <StatCard icon="patients" label="Patients" value={d.counts.patients} note="records in this organization" />
        <StatCard icon="organizations" label="Organizations" value={d.counts.organizations} note="clinics, labs, companies" />
        <StatCard icon="providers" label="Staff logins" value={d.counts.staff} note="people who run the service" />
        <StatCard icon="shield" label="Roles in force" value={d.counts.roles_in_force} note="active role assignments" />
        <StatCard icon="link" label="Care relationships" value={d.counts.care_links} note="active, not contested" />
        {d.evidence && <StatCard icon="bolt" label="Recent access checks" value={d.decisions.length} note={`${denied} denied`} />}
      </div>

      {!d.evidence && (
        <div className="notice">Your role does not include the audit and access-check panels, so they are hidden.</div>
      )}

      <div className="grid cols-3">
        {d.evidence && (
          <Card title="Access checks by outcome" sub="The latest checks made by the policy engine">
            {d.decisions.length === 0 ? <div className="empty">No access checks yet.</div> : (
              <Donut centerLabel="checks" slices={[
                { label: "Allowed", value: allowed, color: "#22a35a" },
                { label: "Denied", value: denied, color: "#e5483c" },
              ]} />
            )}
          </Card>
        )}
        <Card title="Audit & compliance" sub="Facts from the audit log and the system">
          <ul className="checklist">
            <li>
              <Icon name="check" size={16} /> Audit chain
              <span className="right">
                {chain ? (chain.ok ? <Pill kind="ok">intact</Pill> : <Pill kind="bad">broken at #{chain.bad_seq}</Pill>)
                  : <Pill kind="warn">not visible to your role</Pill>}
              </span>
            </li>
            <li><Icon name="check" size={16} /> Audit entries <span className="right">{chain?.checked ?? "-"}</span></li>
            {d.evidence && <li><Icon name="check" size={16} /> Denied checks <span className="right">{denied}</span></li>}
            <li><Icon name="check" size={16} /> API <span className="right"><i className={`dot ${api}`} />{api}</span></li>
            <li><Icon name="check" size={16} /> Database <span className="right"><i className={`dot ${database}`} />{database}</span></li>
          </ul>
        </Card>
        {d.evidence && (
          <Card title="Latest audit entries" sub="Newest first; each entry fingerprints the one before">
            <Table headers={["#", "Time", "Action"]} empty="Nothing audited yet."
              rows={d.events.slice(0, 6).map((e) => [e.seq, timeOf(e.at), e.action])} />
          </Card>
        )}
      </div>

      {d.evidence && (
        <div className="grid">
          <Card title="Recent access decisions" sub="Allowed and denied, with the reason">
            <Table headers={["Time (UTC)", "Who", "Operation", "Purpose", "Result", "Reason"]}
              empty="No access checks yet."
              rows={d.decisions.slice(0, 8).map((x) => [
                timeOf(x.at), x.actor, x.operation, x.purpose,
                <Pill key="r" kind={x.allowed ? "ok" : "bad"}>{x.allowed ? "ALLOWED" : "DENIED"}</Pill>, x.reason,
              ])} />
          </Card>
        </div>
      )}

      <div className="grid cols-3">
        {PLANNED.map((p) => (
          <Card key={p.title} className="planned" title={<>{p.title} <Pill kind="warn">not built yet</Pill></>}>
            <div className="empty">{p.note}</div>
          </Card>
        ))}
      </div>
    </>
  );
}
