import { useState } from "react";

/** One step of a claim or a partner request. Who did it is a side, never a person's name. */
export interface Step { what: string; side: string; at: string }

const WHAT: Record<string, string> = {
  sent: "Sent", accept: "Accepted", decline: "Declined", start: "Work started",
  deliver: "Result made available", correct: "Result corrected", review: "Reviewed by a doctor",
  cancel: "Cancelled", approve: "Approved", pay: "Marked as paid", withdraw: "Withdrawn",
  consent_granted: "Patient agreed", consent_agree: "Patient agreed",
  consent_declined: "Patient said no", consent_decline: "Patient said no",
  consent_withdrawn: "Patient took agreement back", consent_withdraw: "Patient took agreement back",
  on_hold: "Put on hold", released: "Taken off hold", hold_escalated: "Hold passed to the administrator",
  identity_erased: "Shared patient details erased",
};
const SIDE: Record<string, string> = {
  clinic: "the clinic", partner: "the partner", payer: "the payer", patient: "the patient", platform: "Zoiko",
};

/** A button that shows every step of one record, oldest first. Loaded when asked for. */
export function StepHistory({ label, load }: { label: string; load: () => Promise<Step[]> }) {
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle() {
    if (steps !== null) { setSteps(null); return; }
    setBusy(true); setError("");
    try { setSteps(await load()); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the history"); }
    finally { setBusy(false); }
  }
  return (
    <div className={steps === null ? "step-history" : "step-history open"}>
      <button type="button" className="btn ghost" aria-expanded={steps !== null} disabled={busy} onClick={() => void toggle()}>
        {steps === null ? "History" : "Hide history"}<span className="sr-only"> of {label}</span>
      </button>
      {error && <div className="error" role="alert">{error}</div>}
      {steps !== null && (steps.length === 0 ? <p>No steps recorded.</p> : (
        <ol>
          {steps.map((s, i) => (
            <li key={`${s.at}-${i}`}>
              {WHAT[s.what] ?? s.what.replace(/_/g, " ")}
              <small> · by {SIDE[s.side] ?? s.side} · {new Date(s.at).toLocaleString()}</small>
            </li>
          ))}
        </ol>
      ))}
    </div>
  );
}
