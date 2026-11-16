import { useCallback, useEffect, useState } from "react";
import { ApiError, fileRights, getRights, withdrawRights, type RightsRequest } from "../api";
import { Card, Pill } from "../ui";

// The seven kinds of request the specification lists (GHE-06, data rights).
const TYPES: [string, string][] = [
  ["access", "See the data you hold about me"],
  ["correction", "Correct my data"],
  ["portability", "Give me my data to take elsewhere"],
  ["restriction", "Limit how you use my data"],
  ["objection", "I object to how you use my data"],
  ["consent_withdrawal", "I withdraw my consent"],
  ["erasure", "Delete my data"],
];
const WORDS = Object.fromEntries(TYPES);

const STATE: Record<RightsRequest["state"], { text: string; kind: "warn" | "info" | "ok" | "bad" }> = {
  received: { text: "Received", kind: "warn" },
  in_progress: { text: "Being looked at", kind: "info" },
  completed: { text: "Done", kind: "ok" },
  declined: { text: "Declined", kind: "bad" },
  withdrawn: { text: "Withdrawn", kind: "info" },
};

function friendly(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) return "You already have that request open.";
  if (e instanceof ApiError && e.status === 429) return "You have several requests open already. Wait for the clinic to answer one.";
  if (e instanceof ApiError && e.status === 403) return "Only you can make a request about your own data, from your own account.";
  return e instanceof Error ? e.message : "That did not work";
}

/** Formal requests about the patient's own data, and where each one stands. */
export function RightsRequests() {
  const [rows, setRows] = useState<RightsRequest[] | null>(null);
  const [type, setType] = useState("access");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const load = useCallback(async () => {
    try { setRows(await getRights()); }
    catch { setRows([]); }                       // a guardian, or someone without a record, has none
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setDone("");
    try { const made = await fileRights(type, note); setDone(`Sent. Your reference is ${made.ref}.`); setNote(""); await load(); }
    catch (err) { setError(friendly(err)); }
    finally { setBusy(false); }
  }

  async function withdraw(id: string) {
    setError("");
    try { await withdrawRights(id); await load(); } catch (err) { setError(friendly(err)); }
  }

  return (
    <Card title="Your data rights" sub="Ask your clinic something formal about your own data. The clinic checks who is asking, then does the work and tells you what it did. Nothing here is done automatically. Where your country sets a deadline, you will see the date.">
      <form className="profile-form" onSubmit={(e) => void send(e)}>
        <label>What do you want?
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map(([code, text]) => <option key={code} value={code}>{text}</option>)}
          </select>
        </label>
        <label>Anything the clinic should know (optional, up to 500 characters)
          <textarea value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} style={{ minHeight: 70 }} />
        </label>
        {error && <div className="error" role="alert">{error}</div>}
        {done && <div className="success" role="status">{done}</div>}
        <div><button className="btn" type="submit" disabled={busy}>{busy ? "Sending…" : "Send the request"}</button></div>
      </form>
      {rows && rows.length > 0 && (
        <div style={{ marginTop: 14 }}>
          {rows.map((r) => (
            <div key={r.id} className="appt">
              <div className="info">
                <b>{r.ref} <Pill kind={STATE[r.state].kind}>{STATE[r.state].text}</Pill></b>
                <small>{WORDS[r.type] ?? r.type}. Sent {r.created_at.slice(0, 10)}.</small>
                {r.due_at && (r.state === "received" || r.state === "in_progress") && <small>The clinic should answer by {r.due_at.slice(0, 10)}.</small>}
                {r.identity_checked_at && r.state !== "withdrawn" && <small>The clinic has checked who you are.</small>}
                {r.decision_note && <small>The clinic says: {r.decision_note}</small>}
              </div>
              {(r.state === "received" || r.state === "in_progress") && (
                <button className="btn ghost" onClick={() => void withdraw(r.id)}>Withdraw</button>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
