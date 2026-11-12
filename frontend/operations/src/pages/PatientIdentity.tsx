import { useCallback, useEffect, useState } from "react";
import { getIdentity, recordIdentityCheck, withdrawIdentityCheck } from "../api";
import type { IdentityData } from "../consoleTypes";
import { Card, Pill } from "../ui";

const DOCUMENT: [string, string][] = [
  ["passport", "Passport"], ["national_id", "National identity card"],
  ["driving_licence", "Driving licence"], ["other_document", "Another official document"],
];
const WITHDRAW: [string, string][] = [
  ["mistake", "The check was a mistake"], ["fraud_concern", "A fraud concern"],
  ["document_expired", "The document had expired"], ["other", "Another reason"],
];
const WHY: Record<string, string> = {
  withdrawn: "The last check was withdrawn.",
  check_failed: "The last check did not pass.",
  expired: "The last check has expired.",
  details_changed: "The name or birth date changed after the check, so it needs repeating.",
};
const when = (iso: string) => new Date(iso).toLocaleString();

/** Whether this patient's identity has been checked, by whom, and the history of checks. Verified is only
 *  ever set by staff recording a check against an official document: a patient cannot set it. */
export function PatientIdentity({ person, onChanged }: { person: string; onChanged: () => void }) {
  const [data, setData] = useState<IdentityData | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ evidence_type: "passport", outcome: "passed", valid_days: "" });
  const [reason, setReason] = useState("mistake");
  const [withdrawing, setWithdrawing] = useState(false);

  const load = useCallback(async () => {
    try { setData(await getIdentity(person)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, [person]);
  useEffect(() => { setData(null); void load(); }, [load]);

  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setNote("");
    try { await work(); setNote(success); await load(); onChanged(); }
    catch (e) {
      setError(e instanceof Error && e.message.includes("temporarily")
        ? "This patient's record is under a possible-duplicate review. Settle it on the Duplicates page first."
        : e instanceof Error && e.message.includes("Validation")
        ? "A name and a birth date must be saved before an identity can be checked."
        : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }

  const id = data?.identity;
  return (
    <Card title="Identity" sub="Verified means a member of staff checked an official document. Only the fact is kept: never the document or its number.">
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
      {id === undefined ? <div className="empty">Loading…</div> : (
        <>
          <p>
            <Pill kind={id.status === "verified" ? "ok" : "warn"}>{id.status === "verified" ? "Verified" : "Not verified"}</Pill>{" "}
            {id.status === "verified"
              ? `Checked by ${id.clinic || "a clinic"} on ${id.checked_on}${id.valid_until ? `, valid until ${id.valid_until}` : ""}.`
              : WHY[id.why_not] ?? "No identity check has been recorded."}
          </p>
          {id.under_review && (
            <div className="notice" role="status">A possible duplicate of this patient is waiting for a decision (see Duplicates). An identity cannot be checked until it is settled.</div>
          )}
          <div className="form-row">
            <label>Document
              <select value={form.evidence_type} onChange={(e) => setForm({ ...form, evidence_type: e.target.value })} aria-label="Document checked">
                {DOCUMENT.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select>
            </label>
            <label>Result
              <select value={form.outcome} onChange={(e) => setForm({ ...form, outcome: e.target.value })} aria-label="Result of the check">
                <option value="passed">Passed</option><option value="failed">Did not pass</option>
              </select>
            </label>
            <label>Valid for (days)
              <input type="number" min={1} max={3650} placeholder="1095" value={form.valid_days} aria-label="Days the check stays valid"
                onChange={(e) => setForm({ ...form, valid_days: e.target.value })} style={{ maxWidth: 110 }} />
            </label>
            <button className="btn" disabled={busy || id.under_review}
              onClick={() => void run(() => recordIdentityCheck(person, {
                evidence_type: form.evidence_type, outcome: form.outcome,
                ...(form.valid_days ? { valid_days: Number(form.valid_days) } : {}),
              }), form.outcome === "passed" ? "Recorded: identity checked." : "Recorded: the check did not pass.")}>
              Record identity check
            </button>
          </div>
          {id.status === "verified" && (
            <div className="form-row">
              {!withdrawing
                ? <button className="btn ghost" onClick={() => setWithdrawing(true)}>Withdraw the check</button>
                : (
                  <>
                    <select value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Why the check is withdrawn">
                      {WITHDRAW.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                    </select>
                    <button className="btn" disabled={busy} onClick={() => { setWithdrawing(false); void run(() => withdrawIdentityCheck(person, reason), "The check was withdrawn."); }}>Yes, withdraw it</button>
                    <button className="btn ghost" onClick={() => setWithdrawing(false)}>Cancel</button>
                  </>
                )}
            </div>
          )}
          {data?.history && data.history.length > 0 && (
            <div className="sub">
              <b>History of checks</b>
              <ul>{data.history.map((h) => (
                <li key={h.id}>{when(h.checked_at)}: {DOCUMENT.find(([v]) => v === h.evidence_type)?.[1] ?? h.evidence_type},
                  {" "}{h.outcome === "passed" ? "passed" : "did not pass"}, by {h.by}{h.clinic ? ` (${h.clinic})` : ""}
                  {h.withdrawn ? `; withdrawn (${WITHDRAW.find(([v]) => v === h.withdraw_reason)?.[1] ?? h.withdraw_reason})` : ""}</li>
              ))}</ul>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
