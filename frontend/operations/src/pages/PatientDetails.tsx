import { useEffect, useState } from "react";
import { getProfile, saveProfile } from "../api";
import { Card } from "../ui";

/** Staff enter or correct a patient's name, birth date and phone. The details are stored encrypted. */
export function PatientDetails({ person, onClose, onSaved }: {
  person: string; onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [birth, setBirth] = useState("");
  const [phone, setPhone] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setReady(false); setError("");
    getProfile(person)
      .then((p) => { if (live) { setName(p.name); setBirth(p.birth_date); setPhone(p.phone); setReady(true); } })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : "Could not load the details"); });
    return () => { live = false; };
  }, [person]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await saveProfile(person, { name, birth_date: birth, phone }); onSaved(); }
    catch (err) {
      setError(err instanceof Error && err.message.includes("Validation")
        ? "Check the details: a name is required, the birth date must be a real past date (YYYY-MM-DD), and the phone digits only."
        : err instanceof Error ? err.message : "Could not save");
    } finally { setBusy(false); }
  }

  return (
    <Card title="Patient details" sub="Stored encrypted. Only people allowed to see this patient can read them, and every save is recorded.">
      <form className="add-form" onSubmit={(e) => void submit(e)}>
        <label>Full name
          <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required disabled={!ready} />
        </label>
        <label>Birth date
          <input type="date" value={birth} onChange={(e) => setBirth(e.target.value)} disabled={!ready} />
        </label>
        <label>Phone
          <input value={phone} maxLength={20} onChange={(e) => setPhone(e.target.value)} disabled={!ready} />
        </label>
        {error && <div className="error" role="alert">{error}</div>}
        <div>
          <button className="btn" type="submit" disabled={busy || !ready || !name.trim()}>{busy ? "Saving…" : "Save details"}</button>{" "}
          <button className="btn ghost" type="button" onClick={onClose}>Close</button>
        </div>
      </form>
    </Card>
  );
}
