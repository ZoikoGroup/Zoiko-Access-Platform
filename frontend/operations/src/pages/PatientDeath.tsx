import { useEffect, useState } from "react";
import { getDeath, recordDeath, withdrawDeath, type DeathInfo } from "../api";
import { Card } from "../ui";

const SOURCES: [string, string][] = [
  ["death_certificate", "A death certificate or medical certificate of cause of death"],
  ["hospital_notice", "A notice from a hospital or another clinic"],
  ["family_notice", "The family told the clinic"],
  ["other", "Another source"],
];

/** Recording that a patient has died (DEV-333): an administrator records it, a second one withdraws a mistake. */
export function PatientDeath({ person, onChanged }: { person: string; onChanged: () => void }) {
  const [info, setInfo] = useState<DeathInfo | null>(null);
  const [day, setDay] = useState("");
  const [source, setSource] = useState("death_certificate");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => { getDeath(person).then((r) => setInfo(r.death)).catch(() => setInfo(null)); }, [person]);
  async function run(fn: () => Promise<{ death: DeathInfo; bookings_cancelled?: number }>, done: (n: number) => string) {
    setBusy(true); setError(""); setNotice("");
    try { const r = await fn(); setInfo({ ...info, ...r.death } as DeathInfo); setNotice(done(r.bookings_cancelled ?? 0)); onChanged(); }
    catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? "Check the date: it cannot be in the future." : code === "POLICY_DENIED" ? "Another administrator must do this." : code === "CONFLICT_VERSION" ? "A different date is already recorded. Another administrator must withdraw it first." : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }
  if (!info) return null;
  return (
    <Card title="Death">
      {info.died_on ? (
        <>
          <p>Recorded as having died on <b>{info.died_on}</b> ({SOURCES.find(([k]) => k === info.source)?.[1] ?? info.source}), by {info.recorded_by_name || "an administrator"}. Their future appointments were cancelled and they are sent nothing more. Their notes are kept for 8 years after this date.</p>
          {info.can_record && !info.recorded_by_me && (
            <button className="btn ghost" disabled={busy} onClick={() => void run(() => withdrawDeath(person, "recorded_in_error"), () => "Withdrawn: the patient is no longer recorded as having died. Book their appointments again with them.")}>
              Withdraw: recorded in error
            </button>
          )}
          {info.recorded_by_me && <small>Only another administrator can withdraw this if it was a mistake.</small>}
        </>
      ) : info.can_record ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label>Date of death <input className="field" type="date" value={day} onChange={(e) => setDay(e.target.value)} /></label>
          <label>How the clinic knows{" "}
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              {SOURCES.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
            </select>
          </label>
          <label><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I have checked this is the right patient. Their future appointments will be cancelled.</label>
          <div><button className="btn" disabled={busy || !day || !sure} onClick={() => void run(() => recordDeath(person, day, source), (n) => `Recorded. ${n} future appointment${n === 1 ? "" : "s"} cancelled.`)}>Record the death</button></div>
        </div>
      ) : <p>No death is recorded. Only a clinic administrator can record one.</p>}
      {notice && <div className="notice" role="status">{notice}</div>}
      {error && <div className="error" role="alert">{error}</div>}
    </Card>
  );
}
