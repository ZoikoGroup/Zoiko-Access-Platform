import { useState } from "react";
import { changePassword, getEmailStatus, resendVerification, saveProfile, setEmail, type ProfileRow } from "../api";
import { ChangePassword } from "../ChangePassword";
import { EmailCard } from "../EmailCard";
import { Card } from "../ui";

function One({ row, onSaved }: { row: ProfileRow; onSaved: () => Promise<void> }) {
  const [name, setName] = useState(row.name);
  const [birth, setBirth] = useState(row.birth_date);
  const [phone, setPhone] = useState(row.phone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setDone(false);
    try { await saveProfile(row.person, { name, birth_date: birth, phone }); await onSaved(); setDone(true); }
    catch (err) {
      setError(err instanceof Error && err.message.includes("Validation")
        ? "Check your details: a name is required, the birth date must be a real past date, and the phone digits only."
        : err instanceof Error ? err.message : "Could not save");
    } finally { setBusy(false); }
  }

  return (
    <form className="profile-form" onSubmit={(e) => void submit(e)}>
      <label>Full name
        <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required disabled={!row.editable} />
      </label>
      <label>Birth date
        <input type="date" value={birth} onChange={(e) => setBirth(e.target.value)} disabled={!row.editable} />
      </label>
      <label>Phone
        <input value={phone} maxLength={20} onChange={(e) => setPhone(e.target.value)} disabled={!row.editable} />
      </label>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">Saved.</div>}
      {row.editable && (
        <div><button className="btn" type="submit" disabled={busy || !name.trim()}>{busy ? "Saving…" : "Save"}</button></div>
      )}
    </form>
  );
}

const WHY: Record<string, string> = {
  withdrawn: "The last check was withdrawn, so it needs repeating.",
  check_failed: "The last check did not pass. Ask your clinic what to bring.",
  expired: "Your last check has expired, so it needs repeating.",
  details_changed: "Your name or birth date changed after the check, so it needs repeating.",
};

/** Whether a clinic has checked who this patient is. A patient cannot set this themselves. */
function Identity({ row }: { row: ProfileRow }) {
  const id = row.identity;
  if (!id) return null;
  return (
    <div className="notice" role="status" style={{ marginTop: 12 }}>
      {id.status === "verified"
        ? <><b>Identity verified.</b> {id.clinic || "Your clinic"} checked an official document on {id.checked_on}
            {id.valid_until ? `; the check is valid until ${id.valid_until}` : ""}.</>
        : <><b>Identity not verified yet.</b> To verify it, bring an official document (such as a passport or
            driving licence) to your clinic. Nobody can verify you without seeing it, and you cannot verify
            yourself here. {WHY[id.why_not] ?? ""}</>}
    </div>
  );
}

export function Profile({ profiles, onSaved }: { profiles: ProfileRow[]; onSaved: () => Promise<void> }) {
  return (
    <>
      <div className="hero"><div>
        <h1>My Profile</h1>
        <p>Your name, birth date and phone. They are stored encrypted, shown only to you and to the clinic staff and doctors who are allowed to see you, and every look is recorded.</p>
      </div></div>
      <div className="grid">
        {profiles.length === 0 ? (
          <Card title="No patient record"><div className="empty">Your login is not linked to a patient record yet, so there is nothing to show.</div></Card>
        ) : profiles.map((row, i) => (
          <Card key={row.person} title={row.own ? "My details" : (row.name || `Family member ${i}`)}>
            <One row={row} onSaved={onSaved} />
            <Identity row={row} />
          </Card>
        ))}
        <EmailCard load={getEmailStatus} onSave={setEmail} onResend={resendVerification} />
        <ChangePassword onChange={changePassword} />
      </div>
    </>
  );
}
