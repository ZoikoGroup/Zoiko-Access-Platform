import { useCallback, useEffect, useState } from "react";
import { ApiError, getPartnerChecks, recordPartnerCheck, type PartnerCheckName, type PartnerCheckRow } from "../api";
import { Card, Pill } from "../ui";

const NAMES: { id: PartnerCheckName; label: string; hint: string }[] = [
  { id: "identity", label: "Identity", hint: "Who is this partner, and who signs for them?" },
  { id: "legal", label: "Legal", hint: "Is there a contract or agreement that allows it?" },
  { id: "security", label: "Security", hint: "Has their way of keeping the secret been reviewed?" },
];

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "VALIDATION_FAILED" ? "Write a note of 3 to 300 characters saying what you looked at."
    : code === "CONFLICT_VERSION" ? "That partner already has its secret (or was revoked). Refresh the page."
    : code === "POLICY_DENIED" ? "Your account may not record checks."
    : code === "NOT_FOUND" ? "That partner no longer exists. Refresh the page."
    : e instanceof Error ? e.message : "That did not work";
}

function PartnerBlock({ row, canWrite, onDone }: { row: PartnerCheckRow; canWrite: boolean; onDone: () => Promise<void> }) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function save(check: PartnerCheckName) {
    setBusy(check); setError("");
    try { await recordPartnerCheck(row.customer_ref, row.public_id, check, notes[check] ?? ""); setNotes((all) => ({ ...all, [check]: "" })); await onDone(); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(""); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{row.label} <small>for {row.customer_name}</small></b>
        <small>Key {row.key_id}. May send: {row.allowed_types.join(" ")}.</small>
        {error && <small style={{ color: "var(--bad)" }} role="alert">{error}</small>}
      </div>
      {NAMES.map((n) => (
        <div key={n.id} className="form-row" style={{ alignItems: "center" }}>
          <span style={{ minWidth: 80 }}><b>{n.label}</b> {row.checks[n.id] ? <Pill kind="ok">Recorded</Pill> : <Pill kind="warn">Waiting</Pill>}</span>
          {!row.checks[n.id] && canWrite && (
            <>
              <input aria-label={`${n.label} check note for ${row.label}`} placeholder={n.hint} maxLength={300}
                value={notes[n.id] ?? ""} onChange={(ev) => { const v = ev.target.value; setNotes((all) => ({ ...all, [n.id]: v })); }} />
              <button className="btn ghost" disabled={busy !== ""} onClick={() => void save(n.id)}>Record {n.label.toLowerCase()} check</button>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

/** Partners added by a customer wait here until a support person records all three checks. */
export function PartnerChecks({ canWrite }: { canWrite: boolean }) {
  const [rows, setRows] = useState<PartnerCheckRow[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setRows(await getPartnerChecks()); setError(""); }
    catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <Card title="Partner checks"
      sub="A customer's new partner has no secret. Record an identity, a legal and a security check, each with a note of what you looked at, and the customer can then issue the secret. Nothing here is checked automatically: it is your statement, and it is written to the audit log.">
      {error && <div className="error" role="alert">{error}</div>}
      {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
        ? <div className="empty">No partner is waiting for checks.</div>
        : rows.map((r) => <PartnerBlock key={`${r.customer_ref}/${r.public_id}`} row={r} canWrite={canWrite} onDone={load} />)}
    </Card>
  );
}
