import { useState } from "react";
import { decideReferral, getAnswerInbox, getPatients, getReferrals, getServices, sendReferral } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Patient, Pill, Table, shortId, type PillKind } from "../ui";

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
            {(patients.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.patient_name ?? shortId(p.id)}</option>)}
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

type Outcome = "accepted" | "needs_info" | "declined";

function Answer({ id, onDone }: { id: string; onDone: () => void }) {
  const [choice, setChoice] = useState<Outcome | "">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function send(outcome: Outcome) {
    setBusy(true); setError("");
    try { await decideReferral(id, outcome, reason); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  if (!choice) {
    return (
      <span>
        <button className="btn" disabled={busy} onClick={() => void send("accepted")}>Accept</button>{" "}
        <button className="btn ghost" onClick={() => setChoice("needs_info")}>Ask for more</button>{" "}
        <button className="btn ghost" onClick={() => setChoice("declined")}>Decline</button>
        {error && <small style={{ color: "var(--bad)", display: "block" }}>{error}</small>}
      </span>
    );
  }
  return (
    <span>
      <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} aria-label="Reason"
        placeholder={choice === "declined" ? "Why you cannot accept" : "What you need to know"} />{" "}
      <button className="btn" disabled={busy || !reason.trim()} onClick={() => void send(choice)}>
        {choice === "declined" ? "Decline" : "Send question"}
      </button>{" "}
      <button className="btn ghost" onClick={() => setChoice("")}>Back</button>
      {error && <small style={{ color: "var(--bad)", display: "block" }}>{error}</small>}
    </span>
  );
}

function AnswerList({ onAnswered }: { onAnswered: () => void }) {
  const inbox = useLoad(getAnswerInbox, 15_000);
  return (
    <Gate result={inbox}>
      {(rows) => (
        <Card title={`Referrals for you to answer (${rows.length})`}
          sub="You are named to answer these. Accepting means you take the patient on; declining leaves the patient with the doctor who sent them.">
          <Table headers={["Patient", "For", "From", "Urgency", "Reason", "Status", "Your answer"]}
            empty="No referrals are waiting for you."
            rows={rows.map((r) => [
              <Patient key="p" name={r.patient_name} id={r.patient} />,
              `${r.service.name} - ${r.organization.name}`,
              r.responsible?.name || "",
              <Pill key="u" kind={r.urgency === "urgent" ? "bad" : "info"}>{r.urgency}</Pill>,
              r.reason,
              <span key="s"><Pill kind={STATE[r.state]?.kind ?? "info"}>{STATE[r.state]?.text ?? r.state}</Pill>
                {r.escalated && <small style={{ display: "block", color: "var(--bad)" }}>Waiting for over 24 hours</small>}</span>,
              r.state === "accepted" || r.state === "declined"
                ? <span key="a">{r.decision_reason}</span>
                : <Answer key="a" id={r.id} onDone={onAnswered} />,
            ])} />
        </Card>
      )}
    </Gate>
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
              <Patient key="p" name={r.patient_name} id={r.patient} />,
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
        <p>Send a patient to another service and follow what the clinic decides. The patient sees each step in the app. Patients show by name once their details are entered.</p>
      </div></div>
      <div className="grid">
        <AnswerList key={`a${version}`} onAnswered={() => setVersion((v) => v + 1)} />
        <NewReferral onSent={() => setVersion((v) => v + 1)} />
        <SentList key={version} />
      </div>
    </>
  );
}
