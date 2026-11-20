import { useCallback, useEffect, useState } from "react";
import { ApiError, addCoverage, endCoverage, getSharing, setSharing, type CoverageRow, type SharingChoice } from "../api";
import { Card, Pill } from "../ui";

function Row({ c, onChanged }: { c: CoverageRow; onChanged: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  async function end() {
    setBusy(true);
    try { await endCoverage(c.id); await onChanged(); } finally { setBusy(false); setAsking(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{c.insurer}</b>
        <small>{c.plan || "No plan name"} - member number ending {c.member_last4}</small>
        <small>
          {c.valid_from ? `From ${c.valid_from}` : "No start date"}
          {c.valid_until ? `, until ${c.valid_until}` : ", no end date"}
        </small>
      </div>
      <Pill kind={c.in_force ? "info" : "warn"}>{c.in_force ? "Dates current" : "Expired"}</Pill>
      {!asking && <button className="btn ghost" onClick={() => setAsking(true)}>Remove</button>}
      {asking && (
        <span>
          <button className="btn" disabled={busy} onClick={() => void end()}>Yes, remove it</button>{" "}
          <button className="btn ghost" onClick={() => setAsking(false)}>Keep it</button>
        </span>
      )}
    </div>
  );
}

function AddForm({ onAdded }: { onAdded: () => Promise<void> }) {
  const [insurer, setInsurer] = useState("");
  const [plan, setPlan] = useState("");
  const [member, setMember] = useState("");
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await addCoverage({ insurer, plan, member_id: member, valid_from: from, valid_until: until });
      setInsurer(""); setPlan(""); setMember(""); setFrom(""); setUntil("");
      await onAdded();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409
        ? "You already have the most policies we can keep. Remove an old one first."
        : err instanceof ApiError && err.status === 422
          ? "Check the details: the insurer name and member number are required, and the end date cannot be before the start date."
          : err instanceof Error ? err.message : "Could not save");
    } finally { setBusy(false); }
  }

  return (
    <form className="profile-form" onSubmit={(e) => void submit(e)}>
      <label>Insurer
        <input value={insurer} maxLength={80} onChange={(e) => setInsurer(e.target.value)} required />
      </label>
      <label>Plan (optional)
        <input value={plan} maxLength={80} onChange={(e) => setPlan(e.target.value)} />
      </label>
      <label>Member or policy number
        <input value={member} maxLength={40} onChange={(e) => setMember(e.target.value)} required autoComplete="off" />
      </label>
      <label>Valid from (optional)
        <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
      </label>
      <label>Valid until (optional)
        <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
      </label>
      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn" type="submit" disabled={busy || !insurer.trim() || !member.trim()}>
        {busy ? "Saving…" : "Add insurance"}
      </button></div>
    </form>
  );
}

function SharingCard() {
  const [choices, setChoices] = useState<SharingChoice[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setChoices(await getSharing()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load this choice"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function choose(person: string, share: boolean) {
    setBusy(person); setError("");
    try { await setSharing(person, share); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }
  if (choices === null || choices.length === 0) return error ? <div className="error" role="alert">{error}</div> : null;
  return (
    <Card title="Who can see my insurance" sub="You decide. By default the clinic's staff cannot see it.">
      {error && <div className="error" role="alert">{error}</div>}
      {choices.map((c, i) => (
        <label key={c.person} style={{ display: "block", margin: "8px 0" }}>
          <input type="checkbox" checked={c.share_with_staff} disabled={busy === c.person}
            onChange={(e) => void choose(c.person, e.target.checked)} />{" "}
          Let the clinic's staff see {choices.length > 1 ? `the insurance of person ${i + 1}` : "my insurance details"}
        </label>
      ))}
      <small>Staff only ever see the insurer, the plan, the dates and the last four characters of the number. You can change this at any time. Anything already on file stays yours.</small>
    </Card>
  );
}

export function Insurance({ coverage, hasPatient, onChanged }: {
  coverage: CoverageRow[]; hasPatient: boolean; onChanged: () => Promise<void>;
}) {
  return (
    <>
      <div className="hero"><div>
        <h1>Insurance &amp; Claims</h1>
        <p>Keep your insurance on file, and choose whether the clinic's staff may see it. Your member number is stored encrypted and shown only as its last four characters. "Dates current" only means the end date you typed has not passed: we do not check with your insurer.</p>
      </div></div>
      {!hasPatient && <div className="notice">Your login is not linked to a patient record yet, so you cannot add insurance.</div>}
      <div className="grid">
        <Card title={`My insurance (${coverage.length})`}>
          {coverage.length === 0
            ? <div className="empty">No insurance on file yet.</div>
            : coverage.map((c) => <Row key={c.id} c={c} onChanged={onChanged} />)}
        </Card>
        {hasPatient && <SharingCard />}
        {hasPatient && <Card title="Add insurance"><AddForm onAdded={onChanged} /></Card>}
        <Card title="Claims">
          <div className="empty">Claims are not built yet. Today this page only records which insurance you have; it does not check whether a policy is valid with the insurer, and it does not submit or track claims.</div>
        </Card>
      </div>
    </>
  );
}
