import { useCallback, useEffect, useState } from "react";
import { acknowledgeReferral, getReferralInbox, listIncomingReferrals, setReferralInbox } from "../api";
import type { IncomingReferral, ReferralInbox } from "../types";
import { Card, Patient, Pill, Table, type PillKind } from "../ui";

const STATE: Record<IncomingReferral["state"], { text: string; kind: PillKind }> = {
  issued: { text: "Waiting for the doctor", kind: "warn" },
  accepted: { text: "Accepted by a doctor", kind: "ok" },
  needs_info: { text: "Waiting for information", kind: "info" },
  declined: { text: "Declined", kind: "bad" },
};

function Receipt({ r, onDone }: { r: IncomingReferral; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const open = r.state === "issued" || r.state === "needs_info";
  async function mark() {
    setBusy(true); setError("");
    try { await acknowledgeReferral(r.id); await onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  if (!open) return <span>{r.decision_reason}</span>;
  return (
    <span>
      {r.acknowledged_at
        ? <small>Marked received {r.acknowledged_at.slice(0, 10)}. Only the doctor can accept.</small>
        : <button className="btn ghost" disabled={busy} onClick={() => void mark()}>Mark received</button>}
      {r.decision_reason && <small style={{ display: "block" }}>The doctor asked: {r.decision_reason}</small>}
      {error && <small style={{ color: "var(--bad)", display: "block" }}>{error}</small>}
    </span>
  );
}

function WhoAnswers({ onChanged }: { onChanged: () => void }) {
  const [box, setBox] = useState<ReferralInbox | null>(null);
  const [owner, setOwner] = useState("");
  const [fallback, setFallback] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void getReferralInbox().then((b) => { setBox(b); setOwner(b.owner?.id ?? ""); setFallback(b.fallback?.id ?? ""); })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, []);

  async function save() {
    setBusy(true); setError(""); setNote("");
    try {
      const b = await setReferralInbox(owner, fallback);
      setBox(b); setNote("Saved. Referrals already waiting now go to this doctor."); onChanged();
    } catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  if (!box) return error ? <div className="error" role="alert">{error}</div> : null;
  return (
    <Card title="Who answers referrals" sub="Accepting a patient is a doctor's decision. Name one doctor to answer, and a different doctor as the fallback.">
      {!box.owner && <div className="notice" role="status">Nobody is named yet, so no referral can be accepted. {box.can_manage ? "Choose a doctor below." : "Ask your clinic administrator to name one."}</div>}
      {box.owner && <p>Answers: <b>{box.owner.name || box.owner.id}</b>{box.fallback ? <> · Fallback: <b>{box.fallback.name || box.fallback.id}</b></> : <> · No fallback named</>}</p>}
      {box.can_manage ? (
        <div className="form-row">
          <label>Doctor who answers
            <select value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Doctor who answers referrals">
              <option value="">Choose a doctor</option>
              {box.doctors.map((d) => <option key={d.id} value={d.id}>{d.name || d.id}</option>)}
            </select>
          </label>{" "}
          <label>Fallback
            <select value={fallback} onChange={(e) => setFallback(e.target.value)} aria-label="Fallback doctor">
              <option value="">No fallback</option>
              {box.doctors.filter((d) => d.id !== owner).map((d) => <option key={d.id} value={d.id}>{d.name || d.id}</option>)}
            </select>
          </label>{" "}
          <button className="btn" disabled={busy || !owner} onClick={() => void save()}>Save</button>
        </div>
      ) : <small>Only your clinic administrator can change this.</small>}
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
    </Card>
  );
}

export function Referrals() {
  const [rows, setRows] = useState<IncomingReferral[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setRows(await listIncomingReferrals()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load referrals"); }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 10_000);
    return () => clearInterval(timer);
  }, [load]);

  const waiting = (rows ?? []).filter((r) => r.state === "issued").length;
  return (
    <>
      <div className="hero"><div>
        <h1>Referrals</h1>
        <p>Patients that doctors have sent to your services. You can mark one as received, which only says it was seen: the named doctor accepts, asks for more or declines, and the patient and the sending doctor see the answer. A referral nobody answers within 24 hours is flagged and goes to the fallback; it is never closed on its own. {waiting > 0 ? `${waiting} waiting for the doctor.` : ""}</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <WhoAnswers onChanged={() => void load()} />
        {rows === null ? <div className="notice">Loading…</div> : (
          <Card title={`Incoming referrals (${rows.length})`}>
            <Table headers={["Patient", "For service", "From", "Urgency", "Reason", "Status", "Doctor", "Receipt"]}
              empty="No referrals have been sent to your services yet."
              rows={rows.map((r) => [
                <Patient key="p" name={r.patient_name} id={r.patient} />,
                r.service.name,
                `${r.referrer.name} (${r.from_organization.name})`,
                <Pill key="u" kind={r.urgency === "urgent" ? "bad" : "info"}>{r.urgency}</Pill>,
                r.reason,
                <span key="s"><Pill kind={STATE[r.state].kind}>{STATE[r.state].text}</Pill>
                  {r.escalated && <small style={{ display: "block", color: "var(--bad)" }}>Not answered for over 24 hours: with the fallback doctor.</small>}
                  {r.state === "declined" && <small style={{ display: "block" }}>Stays with {r.responsible.name || "the sending doctor"}.</small>}</span>,
                r.owner ? (r.owner.name || r.owner.id) : <Pill key="o" kind="warn">Nobody named</Pill>,
                <Receipt key="d" r={r} onDone={load} />,
              ])} />
          </Card>
        )}
      </div>
    </>
  );
}
