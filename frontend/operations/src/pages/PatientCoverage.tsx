import { useCallback, useEffect, useState } from "react";
import { addPatientCoverage, endPatientCoverage, listPatientCoverage } from "../api";
import type { CoverageRow } from "../types";
import { Card, Pill, Table } from "../ui";

/** A patient's insurance, for the front desk. The member number is shown only by its last four characters. */
export function PatientCoverage({ person }: { person: string }) {
  const [rows, setRows] = useState<CoverageRow[] | null>(null);
  const [shared, setShared] = useState(true);            // false: the patient has not chosen to share it
  const [insurer, setInsurer] = useState("");
  const [plan, setPlan] = useState("");
  const [member, setMember] = useState("");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { const got = await listPatientCoverage(person); setRows(got.coverage); setShared(got.shared); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the insurance"); }
  }, [person]);

  useEffect(() => { setRows(null); void load(); }, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await addPatientCoverage(person, { insurer, plan, member_id: member, valid_until: until });
      setInsurer(""); setPlan(""); setMember(""); setUntil("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally { setBusy(false); }
  }

  async function end(id: string) {
    setBusy(true); setError("");
    try { await endPatientCoverage(id); await load(); }
    catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }

  return (
    <Card title="Insurance" sub="Member numbers are stored encrypted. Claims and checks with the insurer are not built yet.">
      {error && <div className="error" role="alert">{error}</div>}
      {rows !== null && !shared && (
        <div className="notice" role="status">
          This patient has not chosen to share their insurance details with the clinic, so they are not shown here.
          You can still record insurance they give you, but it stays hidden until they share it. They can change this on their Insurance page.
        </div>
      )}
      {rows === null ? <div className="empty">Loading…</div> : !shared ? null : (
        <Table headers={["Insurer", "Plan", "Member no.", "Until", "Status", ""]}
          empty="No insurance on file."
          rows={rows.map((c) => [
            c.insurer, c.plan || "-", `…${c.member_last4}`, c.valid_until ?? "no end date",
            <Pill key="s" kind={c.in_force ? "ok" : "warn"}>{c.in_force ? "In force" : "Expired"}</Pill>,
            <button key="r" className="btn ghost" disabled={busy} onClick={() => void end(c.id)}>Remove</button>,
          ])} />
      )}
      <form className="form-row" onSubmit={(e) => void add(e)}>
        <input aria-label="Insurer" placeholder="Insurer" value={insurer} maxLength={80}
          onChange={(e) => setInsurer(e.target.value)} required />
        <input aria-label="Plan" placeholder="Plan (optional)" value={plan} maxLength={80}
          onChange={(e) => setPlan(e.target.value)} />
        <input aria-label="Member number" placeholder="Member number" value={member} maxLength={40}
          onChange={(e) => setMember(e.target.value)} required autoComplete="off" />
        <input aria-label="Valid until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        <button className="btn" type="submit" disabled={busy || !insurer.trim() || !member.trim()}>Add</button>
      </form>
    </Card>
  );
}
