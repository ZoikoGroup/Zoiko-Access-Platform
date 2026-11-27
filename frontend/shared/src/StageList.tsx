import { useState } from "react";

/** One stored stage of a booking or referral, as the server words it (DEV-360). */
export interface StoredStage { seq: number; state: string; label: string; at: string; by_event: boolean }

/** A button that opens the stages a booking or referral has been through, oldest first. */
export function StageList({ label, load }: { label: string; load: () => Promise<StoredStage[]> }) {
  const [rows, setRows] = useState<StoredStage[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle() {
    if (rows !== null) { setRows(null); return; }
    setBusy(true); setError("");
    try { setRows(await load()); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the stages"); }
    finally { setBusy(false); }
  }
  return (
    <div className={rows === null ? "step-history" : "step-history open"}>
      <button type="button" className="btn ghost" aria-expanded={rows !== null} disabled={busy} onClick={() => void toggle()}>
        {rows === null ? "Stages" : "Hide stages"}<span className="sr-only"> of {label}</span>
      </button>
      {error && <div className="error" role="alert">{error}</div>}
      {rows !== null && (rows.length === 0 ? <p>No stage recorded yet.</p> : (
        <ol>
          {rows.map((s) => (
            <li key={s.seq}>{s.label}<small> · {new Date(s.at).toLocaleString()}</small></li>
          ))}
        </ol>
      ))}
    </div>
  );
}
