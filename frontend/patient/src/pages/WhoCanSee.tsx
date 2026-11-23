import { useCallback, useEffect, useState } from "react";
import { delegateAction, getAccess, setConsent, type AccessData, type DelegateRow } from "../api";
import { Card, Pill } from "../ui";

const SCOPE: Record<string, string> = {
  appointments: "Appointments", messages: "Messages", referrals: "Referrals", insurance: "Insurance",
};
const DURATIONS: [string, number][] = [["Until I stop", 0], ["30 days", 30], ["90 days", 90], ["1 year", 365]];
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

/** Who can see my information: what I have chosen to share, and who may act for me. I can give a family
 *  member LESS access or end it; giving more is the clinic's decision after it has seen proof. */
export function WhoCanSee() {
  const [data, setData] = useState<AccessData | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [days, setDays] = useState<Record<string, number>>({});
  const [editing, setEditing] = useState("");            // the delegate whose access is being narrowed
  const [keep, setKeep] = useState<string[]>([]);
  const [until, setUntil] = useState("");
  const [ending, setEnding] = useState("");

  const load = useCallback(async () => {
    try { setData(await getAccess()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<void>, success: string) {
    setBusy(true); setError(""); setNote("");
    try { await work(); setNote(success); setEditing(""); setEnding(""); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  function startNarrowing(d: DelegateRow) { setEditing(d.id); setKeep(d.scopes); setUntil(""); setEnding(""); }
  function toggle(scope: string) {
    setKeep((k) => (k.includes(scope) ? k.filter((s) => s !== scope) : [...k, scope]));
  }

  if (!data) return <Card title="Who can see my information">{error ? <div className="error" role="alert">{error}</div> : <div className="empty">Loading…</div>}</Card>;
  if (!data.person) return null;                         // a login with no record of its own: nothing to show
  const waiting = data.delegates.filter((d) => d.waiting_for_you);

  return (
    <>
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
      {waiting.map((d) => (
        <div key={d.id} className="notice" role="alert">
          <b>You are now {data.age_of_majority} or over.</b> {d.who} (your {d.relationship}) could see and manage your
          information as your {d.relationship}. Their access is paused until you decide: nothing is shown to them now.
          <div className="form-row">
            <button className="btn" disabled={busy} onClick={() => void run(() => delegateAction(d.id, "keep"), "They keep their access.")}>
              Let them keep access<span className="sr-only"> ({d.who})</span></button>
            <button className="btn ghost" disabled={busy} onClick={() => void run(() => delegateAction(d.id, "end"), "Their access has ended.")}>
              End their access<span className="sr-only"> ({d.who})</span></button>
          </div>
        </div>
      ))}
      <div className="grid">
        <Card title="What I have chosen to share" sub="Nothing is shared and no one is emailed unless you say so. You can stop at any time, or choose a date it ends.">
          {data.consents.map((c) => (
            <div key={c.purpose} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <div className="info">
                <b>{c.label}</b>{" "}
                {c.state === "granted" && <Pill kind="ok">{c.expires_at ? `Allowed until ${day(c.expires_at)}` : "Allowed"}</Pill>}
                {c.state === "expired" && <Pill kind="warn">Ended {c.expires_at ? day(c.expires_at) : ""}</Pill>}
                {c.state === "withdrawn" && <Pill kind="info">Not allowed</Pill>}
              </div>
              <div className="form-row">
                {c.shared
                  ? <button className="btn ghost" disabled={busy} onClick={() => void run(() => setConsent(c.purpose, false), "That has stopped.")}>
                      {c.no_label ?? "Stop sharing"}<span className="sr-only"> ({c.label})</span></button>
                  : <>
                      <label>For how long
                        <select value={days[c.purpose] ?? 0} onChange={(e) => setDays({ ...days, [c.purpose]: Number(e.target.value) })}>
                          {DURATIONS.map(([text, n]) => <option key={n} value={n}>{text}</option>)}
                        </select>
                      </label>
                      <button className="btn" disabled={busy} onClick={() => void run(() => setConsent(c.purpose, true, days[c.purpose] || undefined), "Saved.")}>
                        {c.yes_label ?? "Share"}<span className="sr-only"> ({c.label})</span></button>
                    </>}
              </div>
            </div>
          ))}
        </Card>
        <Card title="People who can act for me" sub="Family members the clinic has approved. You can give them less access, or end it. Only the clinic can give more, after it has seen proof.">
          {data.delegates.length === 0 ? <div className="empty">Nobody acts for you.</div> : data.delegates.map((d) => (
            <div key={d.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <div className="info">
                <b>{d.who}</b> <small>({d.relationship})</small>{" "}
                {d.status === "active" && <Pill kind="ok">Has access</Pill>}
                {d.status === "suspended" && <Pill kind="warn">{d.waiting_for_you ? "Paused: waiting for you" : "Paused by the clinic"}</Pill>}
                {d.status === "expired" && <Pill kind="info">Ended</Pill>}
                <small style={{ display: "block" }}>
                  Can use: {d.scopes.map((s) => SCOPE[s] ?? s).join(", ") || "nothing"}
                  {d.valid_until ? `. Until ${day(d.valid_until)}` : ". No end date"}
                  {d.proof_kind ? `. The clinic recorded proof (${d.proof_kind.replace(/_/g, " ")}).` : ""}
                </small>
              </div>
              {editing === d.id ? (
                <div className="notice" role="group" aria-label={`Give ${d.who} less access`}>
                  <fieldset style={{ border: 0, padding: 0 }}>
                    <legend>They may use</legend>
                    {d.scopes.map((s) => (
                      <label key={s} style={{ marginRight: 12 }}>
                        <input type="checkbox" checked={keep.includes(s)} onChange={() => toggle(s)} /> {SCOPE[s] ?? s}
                      </label>
                    ))}
                  </fieldset>
                  <label>End on (optional, only earlier than now)
                    <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
                  </label>
                  <div className="form-row">
                    <button className="btn" disabled={busy} onClick={() => void run(() => delegateAction(d.id, "narrow", {
                      ...(keep.length !== d.scopes.length ? { scopes: keep } : {}), ...(until ? { valid_until: until } : {}) }),
                      keep.length === 0 ? "Their access has ended." : "Their access has been reduced.")}>Save</button>
                    <button className="btn ghost" onClick={() => setEditing("")}>Cancel</button>
                  </div>
                  {keep.length === 0 && <small>Taking away everything ends their access.</small>}
                </div>
              ) : ending === d.id ? (
                <div className="notice" role="group" aria-label={`End ${d.who}'s access`}>
                  This ends {d.who}'s access to your information. They would need the clinic to approve them again.
                  <div className="form-row">
                    <button className="btn" disabled={busy} onClick={() => void run(() => delegateAction(d.id, "end"), "Their access has ended.")}>Yes, end it</button>
                    <button className="btn ghost" onClick={() => setEnding("")}>Cancel</button>
                  </div>
                </div>
              ) : (d.status !== "expired" && !d.waiting_for_you) && (
                <div className="form-row">
                  <button className="btn ghost" disabled={busy} onClick={() => startNarrowing(d)}>Give less access<span className="sr-only"> ({d.who})</span></button>
                  <button className="btn ghost" disabled={busy} onClick={() => { setEnding(d.id); setEditing(""); }}>End access<span className="sr-only"> ({d.who})</span></button>
                </div>
              )}
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
