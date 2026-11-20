import { getDashboard, getOverview } from "../api";
import type { Dashboard, PortalRole } from "../types";
import { Card, Pill, StatCard, Table } from "../ui";
import { Gate, useLoad } from "../useLoad";

const VERB: Record<string, string> = { read: "see", write: "change", attest: "sign off" };
const SCOPE: Record<string, string> = {
  OWN: "your own only", ORG: "your organisation's", REL: "those you have a relationship with", ANY: "all published",
};
const THING: Record<string, string> = {
  diagnostic: "Tests and imaging", medication: "Medicines", workflow: "Workflow items", comms: "Messages",
  catalog: "Services and listings", partner: "Partner connections", billing: "Bills", coverage: "Insurance cover",
  analytics: "Counts and reports", person: "People", booking: "Appointments", referral: "Referrals",
  evidence: "The audit trail", directory: "The staff directory", ops: "Operations", rights: "Data requests",
  attestation: "Sign-offs", staff: "Staff",
};

function RoleCard({ role }: { role: PortalRole }) {
  return (
    <Card title={<>{role.name} <Pill kind="info">{role.code}</Pill></>} sub={`${role.workspace} · ${role.organization.name}`}>
      <Table label="What this role may reach" headers={["Area", "What you may do", "Which records"]}
        rows={role.can.map((c) => [
          THING[c.resource] ?? c.resource,
          c.verbs.map((v) => VERB[v] ?? v).join(", "),
          SCOPE[c.scope] ?? c.scope,
        ])} empty="This role has no access to anything yet." />
      <h3 style={{ margin: "16px 0 6px", fontSize: 14 }}>What this role never does</h3>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {role.prohibitions.map((p) => <li key={p}>{p}</li>)}
      </ul>
    </Card>
  );
}

function Numbers({ d }: { d: Dashboard }) {
  const hidden = (s: { value: number | null; hidden: boolean }) => s.hidden ? "Hidden" : String(s.value);
  const cards: { icon: string; label: string; value: string; note?: string }[] = [];
  if (d.services) {
    cards.push({ icon: "services", label: "Services listed", value: String(d.services.listed), note: `${d.services.total} in all` });
    cards.push({ icon: "referrals", label: "Waiting for a review", value: String(d.services.waiting_review), note: d.to_review ? `${d.to_review} for you to review` : "none for you to review" });
    cards.push({ icon: "dashboard", label: "Not yet listed", value: String(d.services.not_listed) });
  }
  if (d.requests) {
    cards.push({ icon: "referrals", label: "New requests", value: String(d.requests.new), note: `${d.requests.in_progress} in progress, ${d.requests.delivered} delivered` });
  }
  if (d.plans) {
    cards.push({ icon: "services", label: "Plans switched on", value: String(d.plans.plans_on), note: `${d.plans.plans} in all` });
    cards.push({ icon: "patients", label: "Members enrolled", value: String(d.plans.members), note: `${d.plans.members_ended} ended` });
  }
  if (d.programme) {
    cards.push({ icon: "patients", label: "People enrolled", value: hidden(d.programme.enrolled), note: d.programme.enrolled.hidden ? `Fewer than ${d.programme.min_group}: hidden` : undefined });
    cards.push({ icon: "referrals", label: "Withdrawn", value: hidden(d.programme.withdrawn) });
  }
  if (d.keys && d.webhooks && d.deliveries) {
    cards.push({ icon: "integrations", label: "API keys active", value: String(d.keys.active), note: `${d.keys.total} in all` });
    cards.push({ icon: "referrals", label: "Webhooks active", value: String(d.webhooks.active), note: `${d.webhooks.total} in all` });
    cards.push({ icon: "reports", label: "Deliveries that gave up", value: String(d.deliveries.gave_up), note: `${d.deliveries.delivered} of the last ${d.deliveries.recent} delivered, ${d.deliveries.retrying} retrying` });
  }
  cards.push({ icon: "patients", label: "People on your team", value: String(d.team) });
  return <div className="grid" aria-label="Your numbers">{cards.map((c) => <StatCard key={c.label} {...c} />)}</div>;
}

export function Home() {
  const numbers = useLoad(getDashboard, 60_000);
  const result = useLoad(getOverview, 120_000);
  return (
    <>
      <div className="hero"><div>
        <h1>Overview</h1>
        <p>Here is who you are in Zoiko Access, what your role may reach, and what is and is not connected yet.</p>
      </div></div>
      <Gate result={numbers}>{(d) => <Numbers d={d} />}</Gate>
      <Gate result={result}>
        {(o) => (
          <div className="grid">
            {o.roles.map((r) => <RoleCard key={r.code} role={r} />)}
            <Card title="Connected today">
              {o.connected.length === 0
                ? <div className="empty">Nothing is connected for your kind of account yet. See what it waits for below.</div>
                : <ul style={{ margin: 0, paddingLeft: 18 }}>{o.connected.map((c) => <li key={c}>{c}</li>)}</ul>}
            </Card>
            <Card title={<>Waiting for a decision <Pill kind="warn">not built yet</Pill></>}>
              <ul style={{ margin: 0, paddingLeft: 18 }}>{o.waiting.map((w) => <li key={w}>{w}</li>)}</ul>
            </Card>
          </div>
        )}
      </Gate>
    </>
  );
}
