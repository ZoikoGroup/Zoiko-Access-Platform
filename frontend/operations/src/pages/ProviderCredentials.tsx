import { useCallback, useEffect, useState } from "react";
import { PRIVILEGE_STATE, QualificationForm, QualificationRow, type Privilege, type Qualification } from "@shared/Qualifications";
import { addProviderQualification, decideQualification, getProviderCredentials, grantPrivilege, withdrawPrivilege, type ProviderCredentialsData } from "../api";
import { Card, Pill } from "../ui";

const why = (e: unknown) => {
  const code = (e as { code?: string }).code;
  return code === "POLICY_DENIED" ? "Not allowed: only an administrator may do this, and nobody checks their own."
    : code === "CONFLICT_VERSION" ? "That is already so, or it changed meanwhile."
    : code === "VALIDATION_FAILED" ? "Check what you typed: a note or reason needs at least 3 characters, and dates must be in order."
    : e instanceof Error ? e.message : "That did not work";
};

/** One text box and a button: checking, or withdrawing, always says why. */
function Say({ label, action, disabled, onSend }: { label: string; action: string; disabled: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); onSend(text); }}>
      <input aria-label={label} placeholder={label} value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
      <button className="btn ghost" type="submit" disabled={disabled || text.trim().length < 3}>{action}</button>
    </form>
  );
}

/** One doctor's qualifications and privileges, for the clinic. Only an administrator changes them. */
export function ProviderCredentials({ doctor, name, onClose }: { doctor: string; name: string; onClose: () => void }) {
  const [data, setData] = useState<ProviderCredentialsData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [grant, setGrant] = useState({ service: "", valid_until: "", note: "" });
  const load = useCallback(async () => {
    try { setData(await getProviderCredentials(doctor)); }
    catch (e) { setError(why(e)); }
  }, [doctor]);
  useEffect(() => { setData(null); setError(""); void load(); }, [load]);
  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await load(); }
    catch (e) { setError(why(e)); }
    finally { setBusy(false); }
  }
  const can = !!data?.can_manage;
  return (
    <>
      <Card title={`Qualifications: ${name}`} sub="Patients see only checked ones that have not expired, without numbers or notes."
        action={<button className="btn ghost" onClick={onClose}>Close</button>}>
        {error && <div className="error" role="alert">{error}</div>}
        {data === null ? <div className="empty">{error ? "Could not load." : "Loading…"}</div>
          : data.qualifications.length === 0 ? <div className="empty">Nothing written down yet.</div>
          : data.qualifications.map((q: Qualification) => (
            <QualificationRow key={q.id} q={q}>
              {can && q.state === "recorded" && <Say label={`What did you check for ${q.title}?`} action="Record the check" disabled={busy} onSend={(t) => void run(() => decideQualification(doctor, q.id, "verify", t))} />}
              {can && q.state !== "withdrawn" && <Say label={`Why withdraw ${q.title}?`} action="Withdraw" disabled={busy} onSend={(t) => void run(() => decideQualification(doctor, q.id, "withdraw", t))} />}
            </QualificationRow>
          ))}
        {can && <QualificationForm onAdd={async (q) => { await addProviderQualification(doctor, q); await load(); }} />}
      </Card>
      <Card title={`What ${name} may do here`} sub="A record for your clinic's own governance. It does not yet limit who can be booked.">
        {data === null ? null : data.privileges.length === 0 ? <div className="empty">Nothing recorded yet.</div>
          : data.privileges.map((p: Privilege) => (
            <div key={p.id} className="appt">
              <div className="info">
                <b>{p.service_name} <Pill kind={PRIVILEGE_STATE[p.state].kind}>{PRIVILEGE_STATE[p.state].text}</Pill></b>
                <small>From {p.valid_from}{p.valid_until ? ` until ${p.valid_until}` : ""}{p.note ? ` · ${p.note}` : ""}</small>
                {p.withdrawn_reason && <small>Withdrawn: {p.withdrawn_reason}</small>}
              </div>
              {can && p.state !== "withdrawn" && <Say label={`Why withdraw ${p.service_name}?`} action="Withdraw" disabled={busy} onSend={(t) => void run(() => withdrawPrivilege(doctor, p.id, t))} />}
            </div>
          ))}
        {can && data && (
          <form className="profile-form" style={{ marginTop: 16 }} onSubmit={(e) => { e.preventDefault(); void run(async () => { await grantPrivilege(doctor, grant); setGrant({ service: "", valid_until: "", note: "" }); }); }}>
            <label>Service
              <select value={grant.service} onChange={(e) => setGrant({ ...grant, service: e.target.value })}>
                <option value="">Choose a service</option>
                {data.services.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label>Until (optional)<input type="date" value={grant.valid_until} onChange={(e) => setGrant({ ...grant, valid_until: e.target.value })} /></label>
            <label>Note (optional)<input value={grant.note} maxLength={200} onChange={(e) => setGrant({ ...grant, note: e.target.value })} /></label>
            <button className="btn" type="submit" disabled={busy || !grant.service}>Allow this</button>
          </form>
        )}
      </Card>
    </>
  );
}
