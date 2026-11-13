import { useEffect, useState } from "react";
import { getMyRequests, type MyRequest } from "../api";
import { Card, Pill, type PillKind } from "../ui";

const KIND: Record<MyRequest["kind"], string> = {
  lab_order: "Test or scan", pharmacy_request: "Pharmacy", homecare_referral: "Home care",
};

// Plain words for where each request stands. A ready result is never described as read by a doctor.
function words(r: MyRequest): { text: string; kind: PillKind; hint: string } {
  switch (r.state) {
    case "requested": return { text: "Sent", kind: "info", hint: `Sent to ${r.partner}. Waiting for them to accept it.` };
    case "accepted": return { text: "Accepted", kind: "info", hint: `${r.partner} has accepted it.` };
    case "in_progress": return { text: "In progress", kind: "info", hint: `${r.partner} is working on it.` };
    case "declined": return { text: "Not possible", kind: "bad", hint: `${r.partner} could not do this. Your clinic will get in touch with you.` };
    case "result_available": return { text: "Result ready", kind: "warn", hint: "The result is ready. Your doctor has not reviewed it yet, so please do not take it as medical advice. Your clinic will tell you when they have." };
    case "reviewed": return { text: "Reviewed by your doctor", kind: "ok", hint: "Your doctor has reviewed the result." };
    case "cancelled": return { text: "Cancelled", kind: "info", hint: "Your clinic cancelled this request." };
  }
}

export function MyRequests() {
  const [rows, setRows] = useState<MyRequest[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getMyRequests().then(setRows).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  }, []);
  return (
    <>
      <div className="hero"><div>
        <h1>My Tests &amp; Requests</h1>
        <p>When your clinic asks a laboratory, pharmacy or home-care service to do something for you, it appears here with how far it has got. Results are not shown here: your clinic goes through them with you.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Requests (${rows?.length ?? 0})`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">Nothing has been requested for you.</div>
            : rows.map((r) => {
              const w = words(r);
              return (
                <div className="appt" key={r.id}>
                  <div className="info">
                    <b>{r.label}</b>
                    <small>{KIND[r.kind]} · {r.partner} · asked on {r.created_at.slice(0, 10)}</small>
                    <small>{w.hint}</small>
                  </div>
                  {r.urgency === "urgent" && <Pill kind="warn">Urgent</Pill>}
                  <Pill kind={w.kind}>{w.text}</Pill>
                </div>
              );
            })}
        </Card>
      </div>
    </>
  );
}
