import { useState } from "react";
import { decideRetention, eraseRecords, getRetention, previewErasure, proposeRetention, type ErasurePreview, type RetentionClass, type Staff } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill } from "../ui";
import { NoStaff } from "./Appointments";

function days(n: number | null): string {
  if (n === null) return "no limit";
  return n % 365 === 0 ? `${n / 365} year${n === 365 ? "" : "s"}` : `${n} days`;
}

/** Erasing what is past the period (DEV-351): first look at what would go, then type that exact
 * number. It cannot be undone, and the server refuses if the number has changed meanwhile. */
function Erase({ c, onDone }: { c: RetentionClass; onDone: () => void }) {
  const [found, setFound] = useState<ErasurePreview | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  async function look() {
    setBusy(true); setError(""); setDone(""); setTyped("");
    try { setFound(await previewErasure(c.data_class)); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  async function erase(e: React.FormEvent) {
    e.preventDefault();
    if (!found) return;
    setBusy(true); setError("");
    try {
      const r = await eraseRecords(c.data_class, found.due);
      setDone(`Erased the content of ${r.erased} ${r.erased === 1 ? "record" : "records"}.`);
      setFound(null); setTyped(""); onDone();
    } catch (err) {
      setError(err instanceof Error ? `${err.message}. Look again: the number may have changed.` : "That did not work");
      setFound(null);
    } finally { setBusy(false); }
  }
  return (
    <div className="form-row">
      <button type="button" className="btn ghost" disabled={busy} onClick={() => void look()}>Check what would be erased</button>
      {found && (found.due === 0 ? <p>Nothing would be erased.</p> : (
        <form onSubmit={(e) => void erase(e)}>
          <p><b>{found.due}</b> {found.due === 1 ? "record" : "records"} older than {new Date(found.older_than).toLocaleDateString()} would have their content erased. Who and when stay, and so does the audit trail. Patients on a legal hold are left out. <b>This cannot be undone.</b></p>
          <label>Type {found.due} to confirm
            <input inputMode="numeric" value={typed} onChange={(e) => setTyped(e.target.value)} aria-label={`Confirm erasing ${c.label}`} style={{ maxWidth: 120 }} />
          </label>{" "}
          <button className="btn" type="submit" disabled={busy || typed.trim() !== String(found.due)}>{busy ? "Erasing…" : "Erase now"}</button>
        </form>
      ))}
      {done && <p role="status">{done}</p>}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}

/** One kind of data: the rule in force, any proposal waiting for a second administrator, how many
 * records are past the period and, where erasing is switched on, the way to erase them. */
function Kind({ c, canSet, erasing, onDone }: { c: RetentionClass; canSet: boolean; erasing: boolean; onDone: () => void }) {
  const [period, setPeriod] = useState("");
  const [basis, setBasis] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); setPeriod(""); setBasis(""); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  const n = period.trim() === "" ? null : Number(period);
  const periodOk = n === null || (Number.isInteger(n) && n >= 1 && n <= 36500);
  const basisOk = basis.trim().length >= 3 && basis.trim().length <= 300;
  return (
    <Card title={c.label} sub={`Age counted from ${c.age_from}.`}>
      <p>
        {c.rule ? <>Kept for <b>{days(c.rule.period_days)}</b> <small>({c.rule.basis})</small></>
          : <><Pill kind="warn">no rule</Pill> Kept with no limit until your clinic sets a period.</>}
      </p>
      {c.past_period !== null && (
        <p>{c.past_period === 0 ? "Nothing is past the period."
          : <><b>{c.past_period}</b> {c.past_period === 1 ? "record is" : "records are"} past the period. {c.erasable && erasing
            ? "Nothing is erased until an administrator asks for it below."
            : "The system does not erase these: decide with your data protection lead what to do."}</>}</p>
      )}
      {canSet && erasing && c.erasable && c.rule?.period_days && <Erase c={c} onDone={onDone} />}
      {c.proposal && (
        <div className="form-row">
          <span>Proposed: <b>{days(c.proposal.period_days)}</b> <small>({c.proposal.basis})</small> <Pill kind="warn">waiting for a second administrator</Pill></span>{" "}
          {canSet && <>
            <button className="btn" disabled={busy} onClick={() => void run(() => decideRetention(c.proposal!.public_id, "approve"))}>Approve</button>{" "}
            <button className="btn ghost" disabled={busy} onClick={() => void run(() => decideRetention(c.proposal!.public_id, "withdraw"))}>Withdraw</button>
          </>}
        </div>
      )}
      {canSet && !c.proposal && (
        <div className="form-row">
          <label>Days to keep (empty for no limit)
            <input type="number" min={1} max={36500} value={period} onChange={(e) => setPeriod(e.target.value)} aria-label={`Days to keep ${c.label}`} style={{ maxWidth: 120 }} />
          </label>{" "}
          <label>Basis (the law or policy)
            <input value={basis} maxLength={300} onChange={(e) => setBasis(e.target.value)} aria-label={`Basis for keeping ${c.label}`} />
          </label>{" "}
          <button className="btn" disabled={busy || !periodOk || !basisOk} onClick={() => void run(() => proposeRetention(c.data_class, n, basis.trim()))}>Propose</button>
        </div>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </Card>
  );
}

export function Retention({ staff }: { staff: Staff | null }) {
  const [version, setVersion] = useState(0);
  const result = useLoad(getRetention, 60_000, version);
  if (!staff) return <NoStaff />;
  return (
    <Gate result={result}>
      {(r) => (
        <>
          <div className="hero"><div>
            <h1>Keeping records</h1>
            <p>How long {staff.organizationName} keeps each kind of record. There are no periods until your clinic sets them: one administrator proposes, a different one approves. Records of {r.patients_on_hold} {r.patients_on_hold === 1 ? "patient" : "patients"} under a legal hold are never counted as past their period, and are never erased. {r.deletes_anything
              ? "Erasing is switched on: nothing goes by itself, an administrator has to ask for it."
              : "Erasing is switched off on this system: nothing is deleted."}</p>
            {!r.can_set && <p><small>Only your clinic administrators can propose or approve a period.</small></p>}
          </div></div>
          <div className="grid">
            {r.classes.map((c) => <Kind key={c.data_class} c={c} canSet={r.can_set} erasing={r.deletes_anything} onDone={() => setVersion((v) => v + 1)} />)}
          </div>
        </>
      )}
    </Gate>
  );
}
