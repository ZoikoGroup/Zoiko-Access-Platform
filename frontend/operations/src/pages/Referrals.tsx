import { useCallback, useEffect, useState } from "react";
import { decideReferral, listIncomingReferrals } from "../api";
import type { IncomingReferral } from "../types";
import { Card, Pill, Table, shortId, type PillKind } from "../ui";

const STATE: Record<IncomingReferral["state"], { text: string; kind: PillKind }> = {
  issued: { text: "Needs a decision", kind: "warn" },
  accepted: { text: "Accepted", kind: "ok" },
  needs_info: { text: "Waiting for information", kind: "info" },
  declined: { text: "Declined", kind: "bad" },
};

type Outcome = "accepted" | "needs_info" | "declined";

function Decide({ r, onDone }: { r: IncomingReferral; onDone: () => Promise<void> }) {
  const [choice, setChoice] = useState<Outcome | "">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send(outcome: Outcome) {
    setBusy(true); setError("");
    try { await decideReferral(r.id, outcome, reason); await onDone(); setChoice(""); setReason(""); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  if (r.state === "accepted" || r.state === "declined") return <span>{r.decision_reason}</span>;
  if (!choice) {
    return (
      <span className="row">
        <button className="btn" disabled={busy} onClick={() => void send("accepted")}>Accept</button>{" "}
        <button className="btn ghost" onClick={() => setChoice("needs_info")}>Ask for more</button>{" "}
        <button className="btn ghost" onClick={() => setChoice("declined")}>Decline</button>
        {r.decision_reason && <small style={{ display: "block" }}>You asked: {r.decision_reason}</small>}
        {error && <small style={{ color: "var(--bad)", display: "block" }}>{error}</small>}
      </span>
    );
  }
  return (
    <span>
      <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus
        aria-label="Reason" placeholder={choice === "declined" ? "Why you cannot accept" : "What you need to know"}
        style={{ padding: "7px 10px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg)" }} />{" "}
      <button className="btn" disabled={busy || !reason.trim()} onClick={() => void send(choice)}>
        {choice === "declined" ? "Decline" : "Send question"}
      </button>{" "}
      <button className="btn ghost" onClick={() => setChoice("")}>Back</button>
      {error && <small style={{ color: "var(--bad)", display: "block" }}>{error}</small>}
    </span>
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
        <p>Patients that doctors have sent to your services. Accept, ask for more information, or decline: the patient and the doctor see your answer. {waiting > 0 ? `${waiting} waiting for a decision.` : ""}</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        {rows === null ? <div className="notice">Loading…</div> : (
          <Card title={`Incoming referrals (${rows.length})`}>
            <Table headers={["Patient", "For service", "From", "Urgency", "Reason", "Status", "Your decision"]}
              empty="No referrals have been sent to your services yet."
              rows={rows.map((r) => [
                <span key="p" className="mono">{shortId(r.patient)}</span>,
                r.service.name,
                `${r.referrer.name} (${r.from_organization.name})`,
                <Pill key="u" kind={r.urgency === "urgent" ? "bad" : "info"}>{r.urgency}</Pill>,
                r.reason,
                <Pill key="s" kind={STATE[r.state].kind}>{STATE[r.state].text}</Pill>,
                <Decide key="d" r={r} onDone={load} />,
              ])} />
          </Card>
        )}
      </div>
    </>
  );
}
