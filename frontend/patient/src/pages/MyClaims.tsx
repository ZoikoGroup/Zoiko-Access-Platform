import { useEffect, useState } from "react";
import { answerMyClaim, getMyClaims, type MyClaim } from "../api";
import { Card, Pill, type PillKind } from "../ui";

const money = (minor: number, currency: string) => `${(minor / 100).toFixed(2)} ${currency}`;

function words(c: MyClaim): { text: string; kind: PillKind } {
  if (c.consent === "pending" && c.state === "submitted") return { text: "Needs your agreement", kind: "warn" };
  if (c.consent === "declined") return { text: "You said no", kind: "info" };
  if (c.consent === "withdrawn") return { text: "You took it back", kind: "info" };
  switch (c.state) {
    case "submitted": return { text: "Sent to your insurer", kind: "info" };
    case "approved": return { text: "Approved", kind: "ok" };
    case "paid": return { text: "Paid", kind: "ok" };
    case "declined": return { text: "Not accepted by your insurer", kind: "bad" };
    default: return { text: "Withdrawn", kind: "info" };
  }
}

export function MyClaims() {
  const [rows, setRows] = useState<MyClaim[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const load = () => getMyClaims().then(setRows).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  useEffect(() => { void load(); }, []);
  async function answer(id: string, step: "agree" | "decline" | "withdraw") {
    setBusy(id); setError("");
    try { await answerMyClaim(id, step); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }
  return (
    <>
      <div className="hero"><div>
        <h1>My Claims</h1>
        <p>When your clinic asks your insurer to pay for something, it can only be sent if you agree. Your insurer is told only your member number, what was done, how much and the date. It is never told your name or any medical detail.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Claims (${rows?.length ?? 0})`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">No claims have been made for you.</div>
            : rows.map((c) => {
              const w = words(c);
              return (
                <div className="appt" key={c.id}>
                  <div className="info">
                    <b>{c.label}</b>
                    <small>{c.payer} · {money(c.amount_minor, c.currency)} · service on {c.service_date}</small>
                    {c.consent === "pending" && c.state === "submitted" && (
                      <small><b>Your agreement is needed.</b> If you agree, {c.payer} is told your member number, this service, the amount and the date. If you say no, the claim is cancelled.</small>
                    )}
                    {c.consent === "pending" && c.state === "submitted" && (
                      <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button className="btn" disabled={busy === c.id} onClick={() => void answer(c.id, "agree")}>I agree to this claim</button>
                        <button className="btn ghost" disabled={busy === c.id} onClick={() => void answer(c.id, "decline")}>No, cancel it</button>
                      </span>
                    )}
                    {c.consent === "granted" && c.state === "submitted" && (
                      <span><button className="btn ghost" disabled={busy === c.id} onClick={() => void answer(c.id, "withdraw")}>Take my agreement back</button></span>
                    )}
                  </div>
                  <Pill kind={w.kind}>{w.text}</Pill>
                </div>
              );
            })}
        </Card>
      </div>
    </>
  );
}
