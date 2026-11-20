import { useState } from "react";
import { claimCheck, claimStep, getClaims } from "../api";
import type { Claim, ClaimCheck } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const STATE: Record<Claim["state"], { text: string; kind: "ok" | "warn" | "info" | "bad" }> = {
  submitted: { text: "To decide", kind: "warn" }, approved: { text: "Approved: to pay", kind: "info" },
  declined: { text: "Declined", kind: "bad" }, paid: { text: "Paid", kind: "ok" }, withdrawn: { text: "Withdrawn by the clinic", kind: "info" },
};
const REASONS: [string, string][] = [
  ["not_covered", "Not covered"], ["member_not_found", "Member not found"], ["duplicate", "Duplicate of another claim"],
  ["missing_information", "Information missing"], ["other", "Other"],
];
const CHECK: Record<string, string> = {
  covered: "Covered", no_member: "No member with that number", member_ended: "The member's cover has ended",
  outside_dates: "Outside the member's dates", plan_inactive: "The plan is not active", not_covered: "This kind of service is not covered",
};
const money = (minor: number, currency: string) => `${(minor / 100).toFixed(2)} ${currency}`;

function Row({ c, onChanged }: { c: Claim; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("not_covered");
  const [pay, setPay] = useState("");
  const [paidAmount, setPaidAmount] = useState("");   // in currency units; empty = the full amount
  const [paidOn, setPaidOn] = useState("");
  const [check, setCheck] = useState<ClaimCheck | null>(null);
  async function run(step: "approve" | "decline" | "pay") {
    setBusy(true); setError("");
    try { await claimStep(c.id, step, step === "decline" ? { reason } : step === "pay" ? {
      payment_ref: pay,
      ...(paidAmount.trim() ? { amount_paid_minor: Math.round(Number(paidAmount) * 100) } : {}),
      ...(paidOn ? { paid_on: paidOn } : {}),
    } : {}); onChanged(); }
    catch (err) {
      const code = (err as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? (step === "pay" ? "Give your payment reference (3 to 40 letters, digits or . _ : -)." : "Choose a reason.")
        : code === "CONFLICT_VERSION" ? "That step is not possible now: the claim has moved on."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  async function runCheck() {
    setBusy(true); setError("");
    try { setCheck(await claimCheck(c.id)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not check"); }
    finally { setBusy(false); }
  }
  const state = STATE[c.state];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{c.label} <Pill kind={state.kind}>{state.text}</Pill></b>
        <small>{c.from} · member {c.member_id} · {c.category.replace(/_/g, " ")} · {money(c.amount_minor, c.currency)} · service on {c.service_date} · {c.claim_no}</small>
        {check && <small role="status">Cover check: <b>{CHECK[check.reason] ?? check.reason}</b>{check.plan ? ` (plan ${check.plan})` : ""}.</small>}
        {c.state === "declined" && c.decision_reason && <small>Your reason: {c.decision_reason.replace(/_/g, " ")}</small>}
        {c.payment_ref && <small>Your payment reference: {c.payment_ref}{c.amount_paid_minor != null ? ` · paid ${money(c.amount_paid_minor, c.currency)}` : ""}{c.paid_on ? ` on ${c.paid_on}` : ""}</small>}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {c.state === "submitted" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn ghost" disabled={busy} onClick={() => void runCheck()}>Check cover</button>
          <button className="btn" disabled={busy} onClick={() => void run("approve")}>Approve</button>
          <select aria-label={`Reason for declining ${c.claim_no}`} value={reason} onChange={(e) => setReason(e.target.value)}>
            {REASONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <button className="btn ghost" disabled={busy} onClick={() => void run("decline")}>Decline</button>
        </div>
      )}
      {c.state === "approved" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input className="field" aria-label={`Payment reference for ${c.claim_no}`} placeholder="Your payment reference" value={pay} onChange={(e) => setPay(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
          <input className="field" aria-label={`Amount paid for ${c.claim_no}`} inputMode="decimal" placeholder={`Paid (${c.currency}), empty = in full`} value={paidAmount} onChange={(e) => setPaidAmount(e.target.value)} style={{ width: 170 }} />
          <input className="field" type="date" aria-label={`Date paid for ${c.claim_no}`} value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
          <button className="btn" disabled={busy || pay.trim().length < 3} onClick={() => void run("pay")}>Mark as paid</button>
        </div>
      )}
    </div>
  );
}

export function Claims() {
  const [n, setN] = useState(0);              // changing the key loads everything again
  return <ClaimsInner key={n} onChanged={() => setN(n + 1)} />;
}

function ClaimsInner({ onChanged }: { onChanged: () => void }) {
  const list = useLoad(getClaims, 30_000);
  const waiting = (list.data ?? []).filter((c) => c.state === "submitted").length;
  return (
    <>
      <div className="hero"><div>
        <h1>Claims</h1>
        <p>Claims clinics sent to you. You see the member id, the service, the amount and the date, and nothing about the patient. {waiting > 0 ? `${waiting} waiting for your decision.` : ""}</p>
      </div></div>
      <div className="grid">
        <Card title="Claims to you">
          <Gate result={list}>
            {(rows) => rows.length === 0 ? <div className="empty">No claims yet.</div> : rows.map((c) => <Row key={c.id} c={c} onChanged={onChanged} />)}
          </Gate>
        </Card>
      </div>
    </>
  );
}
