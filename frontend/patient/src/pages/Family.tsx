import { useCallback, useEffect, useState } from "react";
import { ApiError, getFamily, requestFamily, withdrawFamily, type FamilyRow } from "../api";
import { Card, Pill } from "../ui";

const RELATIONSHIP: Record<string, string> = { parent: "Your child", guardian: "Someone you are guardian for", caregiver: "Someone you care for" };

const SCOPE_WORDS: Record<string, string> = {
  appointments: "book and see appointments", messages: "send and read messages",
  referrals: "see referrals", insurance: "see and manage insurance",
};
/** What the clinic allowed this person to do, in words. */
const allowed = (scopes?: string[]) => {
  const list = (scopes ?? []).map((c) => SCOPE_WORDS[c]).filter(Boolean);
  return list.length ? `The clinic allows you to ${list.join(", ")}.` : "The clinic has not allowed anything yet.";
};

function Row({ f, onChanged }: { f: FamilyRow; onChanged: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  async function end() {
    setBusy(true);
    try { await withdrawFamily(f.id); await onChanged(); } finally { setBusy(false); setAsking(false); }
  }
  const waiting = f.status === "proposed" || f.status === "proof_required" || f.status === "approval_pending";
  const paused = f.status === "suspended";
  return (
    <div className="appt">
      <div className="info">
        <b>{f.name || "Family member"}</b>
        <small>{RELATIONSHIP[f.relationship] ?? f.relationship}{f.birth_date ? `, born ${f.birth_date}` : ""}</small>
        <small>{f.status === "proof_required" ? "The clinic needs to see proof of your identity and authority. Bring it in. Nothing is shared until they approve."
          : f.status === "approval_pending" ? "The clinic has seen your proof and is deciding. Nothing is shared until they approve."
          : waiting ? "Waiting for the clinic to check your proof of identity. Nothing is shared until they approve."
          : paused ? "The clinic has paused this link. Contact the clinic to ask why."
          : `${allowed(f.scopes)}${f.valid_until ? ` This lasts until ${f.valid_until.slice(0, 10)}.` : ""} Their medical records are not shared.`}</small>
      </div>
      <Pill kind={waiting ? "warn" : paused ? "bad" : "ok"}>{waiting ? "Waiting for the clinic" : paused ? "Paused" : "Approved"}</Pill>
      {!asking && <button className="btn ghost" onClick={() => setAsking(true)}>{waiting ? "Withdraw" : "Stop acting for them"}</button>}
      {asking && (
        <span>
          <button className="btn" disabled={busy} onClick={() => void end()}>Yes, {waiting ? "withdraw" : "stop"}</button>{" "}
          <button className="btn ghost" onClick={() => setAsking(false)}>Keep it</button>
        </span>
      )}
    </div>
  );
}

function AddForm({ onAdded }: { onAdded: () => Promise<void> }) {
  const [relationship, setRelationship] = useState("parent");
  const [name, setName] = useState("");
  const [birth, setBirth] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setDone(false);
    try { await requestFamily({ relationship, name, birth_date: birth }); setName(""); setBirth(""); setDone(true); await onAdded(); }
    catch (err) {
      setError(err instanceof ApiError && err.status === 409
        ? "You already have the most requests we can hold. Wait for the clinic or withdraw one."
        : err instanceof ApiError && err.status === 422
          ? "Check the details: a name and a real past birth date are needed."
          : err instanceof ApiError && err.status === 403
            ? "Only a patient with a record of their own can add a family member."
            : err instanceof Error ? err.message : "Could not send the request");
    } finally { setBusy(false); }
  }

  return (
    <form className="profile-form" onSubmit={(e) => void submit(e)}>
      <label>Who are they to you?
        <select value={relationship} onChange={(e) => setRelationship(e.target.value)}>
          <option value="parent">My child (I am the parent)</option>
          <option value="guardian">I am their legal guardian</option>
          <option value="caregiver">I care for them</option>
        </select>
      </label>
      <label>Their full name
        <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label>Their birth date
        <input type="date" value={birth} onChange={(e) => setBirth(e.target.value)} required />
      </label>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">Request sent. Bring proof of identity to the clinic so they can approve it.</div>}
      <div><button className="btn" type="submit" disabled={busy || !name.trim() || !birth}>{busy ? "Sending…" : "Ask to add them"}</button></div>
    </form>
  );
}

export function Family({ onChanged }: { onChanged: () => Promise<void> }) {
  const [rows, setRows] = useState<FamilyRow[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setRows(await getFamily()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function changed() { await load(); await onChanged(); }

  const approved = (rows ?? []).filter((f) => f.status === "active" || f.status === "suspended");
  const waiting = (rows ?? []).filter((f) => ["proposed", "proof_required", "approval_pending"].includes(f.status));
  return (
    <>
      <div className="hero"><div>
        <h1>Family Members</h1>
        <p>Act for a child or someone you care for: book their appointments and see their care. The clinic must check your proof of identity first. You can read their details but not change them.</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card title={`People I act for (${approved.length})`}>
          {rows === null ? <div className="empty">Loading…</div> : approved.length === 0
            ? <div className="empty">Nobody yet.</div>
            : approved.map((f) => <Row key={f.id} f={f} onChanged={changed} />)}
        </Card>
        {waiting.length > 0 && (
          <Card title={`Waiting for the clinic (${waiting.length})`}>
            {waiting.map((f) => <Row key={f.id} f={f} onChanged={changed} />)}
          </Card>
        )}
        <Card title="Add a family member"><AddForm onAdded={changed} /></Card>
      </div>
    </>
  );
}
