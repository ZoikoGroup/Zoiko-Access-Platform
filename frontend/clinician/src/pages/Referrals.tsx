import { useState } from "react";
import { getPatients, getReferrals, getServices, sendReferral } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table, shortId, type PillKind } from "../ui";

const STATE: Record<string, { text: string; kind: PillKind }> = {
  issued: { text: "Sent, waiting for the clinic", kind: "warn" },
  accepted: { text: "Accepted", kind: "ok" },
  needs_info: { text: "Clinic needs more information", kind: "info" },
  declined: { text: "Declined", kind: "bad" },
};

function NewReferral({ onSent }: { onSent: () => void }) {
  const patients = useLoad(getPatients, 120_000);
  const services = useLoad(getServices, 120_000);
  const [patient, setPatient] = useState("");
  const [service, setService] = useState("");
  const [urgency, setUrgency] = useState("routine");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setDone("");
    try {
      const made = await sendReferral({ patient, service, reason, urgency });
      setDone(`Sent to ${made.referral.service.name} at ${made.referral.organization.name}.`);
      setReason(""); setPatient(""); setService("");
      onSent();
    } catch (err) {
      const code = err instanceof Error && "code" in err ? (err as { code?: string }).code : "";
      setError(code === "CONFLICT_VERSION"
        ? "This patient already has an open referral to that service."
        : err instanceof Error ? err.message : "Could not send the referral");
    } finally { setBusy(false); }
  }

  return (
    <Card title="Refer a patient" sub="Send one of your patients to another service. The clinic that runs it will accept, ask for more, or decline.">
      <form className="ref-form" onSubmit={(e) => void submit(e)}>
        <label>Patient
          <select value={patient} onChange={(e) => setPatient(e.target.value)} required>
            <option value="">Choose one of your patients</option>
            {(patients.data ?? []).map((p) => <option key={p.id} value={p.id}>{shortId(p.id)}</option>)}
          </select>
        </label>
        <label>Send to
          <select value={service} onChange={(e) => setService(e.target.value)} required>
            <option value="">Choose a service</option>
            {(services.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name} - {s.organization.name}</option>)}
          </select>
        </label>
        <label>How urgent
          <select value={urgency} onChange={(e) => setUrgency(e.target.value)}>
            <option value="routine">Routine</option>
            <option value="urgent">Urgent</option>
          </select>
        </label>
        <label>Reason for the referral
          <textarea value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} required
            placeholder="What the receiving doctor needs to know" />
        </label>
        {error && <div className="error" role="alert">{error}</div>}
        {done && <div className="success" role="status">{done}</div>}
        <div><button className="btn" type="submit" disabled={busy || !patient || !service || !reason.trim()}>
          {busy ? "Sending…" : "Send referral"}
        </button></div>
      </form>
    </Card>
  );
}

function SentList() {
  const sent = useLoad(getReferrals, 15_000);
  return (
    <Gate result={sent}>
      {(rows) => (
        <Card title={`Your referrals (${rows.length})`}>
          <Table headers={["Patient", "Sent to", "Urgency", "Status", "Clinic's note", "Sent"]}
            empty="You have not referred anyone yet."
            rows={rows.map((r) => [
              <span key="p" className="mono">{shortId(r.patient)}</span>,
              `${r.service.name} - ${r.organization.name}`,
              <Pill key="u" kind={r.urgency === "urgent" ? "bad" : "info"}>{r.urgency}</Pill>,
              <Pill key="s" kind={STATE[r.state]?.kind ?? "info"}>{STATE[r.state]?.text ?? r.state}</Pill>,
              r.decision_reason || "",
              r.created_at.slice(0, 10),
            ])} />
        </Card>
      )}
    </Gate>
  );
}

export function Referrals() {
  const [version, setVersion] = useState(0);        // a new number reloads the list below
  return (
    <>
      <div className="hero"><div>
        <h1>Referrals</h1>
        <p>Send a patient to another service and follow what the clinic decides. The patient is told at each step. Patients show as ids: names are not stored yet.</p>
      </div></div>
      <div className="grid">
        <NewReferral onSent={() => setVersion((v) => v + 1)} />
        <SentList key={version} />
      </div>
    </>
  );
}
