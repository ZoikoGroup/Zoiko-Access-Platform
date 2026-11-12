import { getBilling } from "../api";
import type { BillingData, BillingPatient, CoverState } from "../consoleTypes";
import { Gate, useLoad } from "../useLoad";
import { Card, Patient, Pill, StatCard, Table, type PillKind } from "../ui";

const STATE: Record<CoverState, { text: string; kind: PillKind }> = {
  covered: { text: "Insured", kind: "ok" },
  ending_soon: { text: "Ending soon", kind: "warn" },
  none_in_force: { text: "No insurance in force", kind: "bad" },
  not_shared: { text: "Patient has not shared", kind: "info" },
};

const day = (iso: string | null) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString() : "no end date");

export function BillingClaims() {
  const result = useLoad(getBilling, 30_000);
  return <Gate result={result}>{(d) => <View data={d} />}</Gate>;
}

function View({ data }: { data: BillingData }) {
  const c = data.counts;
  const rows = (list: BillingPatient[]) => list.map((p) => [
    <Patient key="p" name={p.patient_name} id={p.id} />,
    <Pill key="s" kind={STATE[p.state].kind}>{STATE[p.state].text}</Pill>,
    p.state === "not_shared" ? "Not shared with staff" : p.insurer ? `${p.insurer}${p.plan ? `, ${p.plan}` : ""}` : "None",
    p.state === "covered" || p.state === "ending_soon" ? day(p.valid_until) : "",
    p.upcoming_visit ? "Yes" : "No",
  ]);
  const headers = ["Patient", "Insurance", "Insurer and plan", "Valid until", "Visit coming"];
  return (
    <>
      <div className="hero"><div>
        <h1>Billing &amp; Claims</h1>
        <p>What this clinic can say about its patients' insurance today. A patient's insurance is shown only when
          they chose to share it with staff; for everyone else this page says only that they have not shared, and
          never whether they have insurance. Nothing is checked with an insurer, and member numbers are never shown.</p>
      </div></div>
      <div className="grid stats">
        <StatCard icon="patients" label="Patients" value={c.patients} note="at this clinic" />
        <StatCard icon="shield" label="Insured" value={c.covered} note="shared, in force" />
        <StatCard icon="bolt" label="Ending soon" value={c.ending_soon} note={`within ${data.ending_soon_days} days`} />
        <StatCard icon="link" label="No insurance in force" value={c.none_in_force} note="shared, nothing current" />
        <StatCard icon="providers" label="Not shared" value={c.not_shared} note="the patient decides" />
      </div>
      <div className="grid">
        <Card title={`Ask before the visit (${c.needs_attention})`}
          sub="Patients with a visit still to come whose insurance is missing, ending soon, or not shared with the clinic.">
          <Table headers={headers} empty="Nobody with a visit coming needs a call about insurance." rows={rows(data.attention)} />
        </Card>
        <Card title={`All patients (${c.patients})`}>
          <Table headers={headers} empty="No patients yet." rows={rows(data.patients)} />
        </Card>
        <Card title={<>Invoices, payments and claims <Pill kind="warn">not built yet</Pill></>}>
          <div className="empty">These are a later phase. They wait on the first market and on Zoiko's legal role
            for each service, which the lead has not decided yet.</div>
        </Card>
      </div>
    </>
  );
}
