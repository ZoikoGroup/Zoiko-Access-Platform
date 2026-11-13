import { useState } from "react";
import { getPartnerRequests, reviewPartnerResult } from "../api";
import type { PartnerRequest } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const KIND: Record<string, string> = {
  lab_order: "Laboratory or imaging order", pharmacy_request: "Pharmacy request", homecare_referral: "Home-care referral",
};
const STATE: Record<PartnerRequest["state"], { text: string; kind: "ok" | "warn" | "info" | "bad" }> = {
  requested: { text: "Sent, waiting for the partner", kind: "info" }, accepted: { text: "Accepted", kind: "info" },
  declined: { text: "Declined", kind: "bad" }, in_progress: { text: "In progress", kind: "info" },
  result_available: { text: "Result ready: waiting for you", kind: "warn" },
  reviewed: { text: "Reviewed", kind: "ok" }, cancelled: { text: "Cancelled", kind: "info" },
};

function Row({ r, onChanged }: { r: PartnerRequest; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function review() {
    setBusy(true); setError("");
    try { await reviewPartnerResult(r.id); onChanged(); }
    catch (err) {
      const code = (err as { code?: string }).code;
      setError(code === "POLICY_DENIED" ? "You can only review while you are treating this patient (not when following up read only)."
        : code === "CONFLICT_VERSION" ? "There is no ready result to review."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  const state = STATE[r.state];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{r.label} <Pill kind={state.kind}>{state.text}</Pill>{r.urgency === "urgent" && <> <Pill kind="warn">urgent</Pill></>}</b>
        <small>{KIND[r.kind]} · {r.partner} · {r.patient_name || r.patient} · order {r.token}</small>
        {r.result_ref && <small>The partner holds the result under reference {r.result_ref}. Opening it is done with the partner, outside this screen.</small>}
        {r.state === "declined" && r.decision_note && <small>Reason given: {r.decision_note}</small>}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {r.state === "result_available" && (
        <div>
          <button className="btn" disabled={busy} onClick={() => void review()}>I have reviewed this result</button>
          <small style={{ display: "block", marginTop: 4 }}>This records that you, a clinician, have reviewed it. A result being ready is never treated as reviewed.</small>
        </div>
      )}
    </div>
  );
}

export function PartnerResults() {
  const [n, setN] = useState(0);              // changing the key loads everything again
  return <PartnerResultsInner key={n} onChanged={() => setN(n + 1)} />;
}

function PartnerResultsInner({ onChanged }: { onChanged: () => void }) {
  const list = useLoad(getPartnerRequests, 30_000);
  const waiting = (list.data ?? []).filter((r) => r.state === "result_available").length;
  return (
    <>
      <div className="hero"><div>
        <h1>Partner results</h1>
        <p>Requests your clinic sent to laboratories, pharmacies and home-care partners for your patients, and where each stands. {waiting > 0 ? `${waiting} ready for you to review.` : ""}</p>
      </div></div>
      <div className="grid">
        <Card title="Requests for your patients">
          <Gate result={list}>
            {(rows) => rows.length === 0 ? <div className="empty">Nothing sent for your patients yet.</div> : rows.map((r) => <Row key={r.id} r={r} onChanged={onChanged} />)}
          </Gate>
        </Card>
      </div>
    </>
  );
}
