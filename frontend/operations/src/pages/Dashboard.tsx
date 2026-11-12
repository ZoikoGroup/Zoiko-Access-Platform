import { getDashboard } from "../api";
import type { Dashboard as DashboardData, Health } from "../types";
import { Gate, useLoad } from "../useLoad";
import type { PageId } from "../nav";
import { Card, Donut, Icon, Pill, StatCard, Table, timeOf } from "../ui";

const PLANNED = [
  { title: "Claims processed", note: "A later commercial phase." },
  { title: "Patients by region", note: "Needs more than one cell; not planned yet." },
];

export function Dashboard({ tenantName, api, database, onGo }: {
  tenantName: string; api: Health; database: Health; onGo: (page: PageId) => void;
}) {
  const result = useLoad(getDashboard);
  return (
    <Gate result={result}>
      {(d) => <View d={d} tenantName={tenantName} api={api} database={database} onGo={onGo} />}
    </Gate>
  );
}

const ATTENTION: { key: keyof DashboardData["attention"]; text: (n: number) => string; page: PageId }[] = [
  { key: "bookings_to_confirm", page: "appointments", text: (n) => `${n} appointment${n === 1 ? "" : "s"} waiting for you to confirm` },
  { key: "messages", page: "messages", text: (n) => `${n} conversation${n === 1 ? "" : "s"} with a new message from a patient` },
  { key: "referrals_to_decide", page: "referrals", text: (n) => `${n} referral${n === 1 ? "" : "s"} waiting for a decision` },
  { key: "data_requests", page: "rights", text: (n) => `${n} data request${n === 1 ? "" : "s"} from patients waiting for the clinic` },
  { key: "family_requests", page: "family", text: (n) => `${n} family request${n === 1 ? "" : "s"} waiting for proof to be checked` },
  { key: "waiting_list", page: "waitlist", text: (n) => `${n} ${n === 1 ? "person" : "people"} on a waiting list` },
];

function View({ d, tenantName, api, database, onGo }: {
  d: DashboardData; tenantName: string; api: Health; database: Health; onGo: (page: PageId) => void;
}) {
  const late = d.late.bookings + d.late.messages;
  const open = ATTENTION.filter((a) => d.attention[a.key] > 0);
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

      <div className="grid">
        <Card title="Needs your attention" sub={open.length === 0 ? "Nothing is waiting for you." : "Work waiting for someone at the clinic."}>
          {late > 0 && (
            <div className="appt" role="alert">
              <div className="info"><b>{late} item{late === 1 ? " is" : "s are"} running late</b>
                <span className="sub">{d.late.bookings} booking{d.late.bookings === 1 ? "" : "s"} and {d.late.messages} message{d.late.messages === 1 ? "" : "s"} waiting more than {d.late.hours} hours</span></div>
              <button className="btn" onClick={() => onGo("workflows")}>Open the work queue</button>
            </div>
          )}
          {open.length === 0 ? <div className="empty">All clear.</div> : open.map((a) => (
            <div className="appt" key={a.key}>
              <div className="info"><b>{a.text(d.attention[a.key])}</b></div>
              <button className="btn" onClick={() => onGo(a.page)}>Open</button>
            </div>
          ))}
        </Card>
      </div>

      <div className="grid">
        <Card title="Today" sub="Appointments on today's date at your clinics"
          action={<button className="btn ghost" onClick={() => onGo("appointments")}>Open appointments</button>}>
          {d.today.confirmed + d.today.waiting === 0 ? <div className="empty">No appointments today.</div> : (
            <div className="appt">
              <div className="info">
                <b>{d.today.confirmed} confirmed{d.today.waiting > 0 ? `, ${d.today.waiting} still waiting for you` : ""}</b>
                <span className="sub">{d.today.still_to_come} not started yet</span>
              </div>
            </div>
          )}
        </Card>
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
