import type { Referral } from "../api";
import { Card, Pill, type PillKind } from "../ui";

const STATE: Record<Referral["state"], { text: string; kind: PillKind; hint: string }> = {
  issued: { text: "Sent", kind: "warn", hint: "The clinic has your referral and will review it." },
  accepted: { text: "Accepted", kind: "ok", hint: "The clinic accepted it. You can now book with them under Find Healthcare." },
  needs_info: { text: "More information needed", kind: "info", hint: "The clinic asked your doctor for more information." },
  declined: { text: "Declined", kind: "bad", hint: "The clinic could not take this referral. Ask your doctor what to do next." },
};

function Row({ r }: { r: Referral }) {
  const s = STATE[r.state];
  return (
    <div className="appt">
      <div className="info">
        <b>{r.service.name}</b>
        <small>{r.organization.name} - referred by {r.referrer.name} on {r.created_at.slice(0, 10)}</small>
        <small>Reason: {r.reason}</small>
        <small>{s.hint}{r.decision_reason ? ` Clinic's note: ${r.decision_reason}` : ""}</small>
      </div>
      {r.urgency === "urgent" && <Pill kind="bad">Urgent</Pill>}
      <Pill kind={s.kind}>{s.text}</Pill>
    </div>
  );
}

export function Referrals({ referrals }: { referrals: Referral[] }) {
  const open = referrals.filter((r) => r.state === "issued" || r.state === "needs_info");
  const closed = referrals.filter((r) => !open.includes(r));
  return (
    <>
      <div className="hero"><div>
        <h1>My Referrals</h1>
        <p>When your doctor sends you to another service, it appears here, with the clinic's answer.</p>
      </div></div>
      <div className="grid">
        <Card title={`Waiting (${open.length})`}>
          {open.length === 0 ? <div className="empty">Nothing waiting.</div> : open.map((r) => <Row key={r.id} r={r} />)}
        </Card>
        <Card title={`Answered (${closed.length})`}>
          {closed.length === 0 ? <div className="empty">Nothing here yet.</div> : closed.map((r) => <Row key={r.id} r={r} />)}
        </Card>
      </div>
    </>
  );
}
