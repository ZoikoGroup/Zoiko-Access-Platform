import { useState } from "react";
import { decideRetention, getRetention, proposeRetention, type RetentionClass, type Staff } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill } from "../ui";
import { NoStaff } from "./Appointments";

function days(n: number | null): string {
  if (n === null) return "no limit";
  return n % 365 === 0 ? `${n / 365} year${n === 365 ? "" : "s"}` : `${n} days`;
}

/** One kind of data: the rule in force, any proposal waiting for a second administrator, and how
 * many records are past the period. Nothing is ever deleted by the product (DEV-213). */
function Kind({ c, canSet, onDone }: { c: RetentionClass; canSet: boolean; onDone: () => void }) {
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
          : <><b>{c.past_period}</b> {c.past_period === 1 ? "record is" : "records are"} past the period. The system deletes nothing: decide with your data protection lead what to do.</>}</p>
      )}
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
            <p>How long {staff.organizationName} keeps each kind of record. There are no periods until your clinic sets them: one administrator proposes, a different one approves. Records of {r.patients_on_hold} {r.patients_on_hold === 1 ? "patient" : "patients"} under a legal hold are never counted as past their period. The system never deletes anything.</p>
            {!r.can_set && <p><small>Only your clinic administrators can propose or approve a period.</small></p>}
          </div></div>
          <div className="grid">
            {r.classes.map((c) => <Kind key={c.data_class} c={c} canSet={r.can_set} onDone={() => setVersion((v) => v + 1)} />)}
          </div>
        </>
      )}
    </Gate>
  );
}
