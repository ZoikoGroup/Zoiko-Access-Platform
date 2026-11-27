import { useCallback, useEffect, useState } from "react";
import { getWaitRules, listWaitlist, removeFromWaitlist, setWaitRule, setWaitlistPriority, type Staff, type WaitRules, type WaitServiceRule } from "../api";
import type { WaitingRow } from "../types";
import { Card, Patient, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

/** Who is waiting for each service, longest first. Patients appear as ids (names are not stored). */
const JOURNEY: Record<string, string> = {
  waiting: "waiting for a free time", offered: "told a time opened, waiting for the patient",
  booked: "booked", left: "left the list",
};

const LEVELS: Record<number, string> = { 0: "Routine", 1: "Priority", 2: "Urgent" };

/** Staff move someone up the queue. A level above routine needs a reason, written down. */
function Ranking({ row, onDone }: { row: WaitingRow; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<0 | 1 | 2>(row.priority ?? 0);
  const [reason, setReason] = useState(row.priority_reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true); setError("");
    try { await setWaitlistPriority(row.id, level, reason); setOpen(false); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  if (!open) return <button className="btn ghost" onClick={() => setOpen(true)}>Set priority</button>;
  return (
    <span>
      <select value={level} onChange={(e) => setLevel(Number(e.target.value) as 0 | 1 | 2)} aria-label="Priority">
        {[0, 1, 2].map((n) => <option key={n} value={n}>{LEVELS[n]}</option>)}
      </select>{" "}
      {level > 0 && (
        <input value={reason} maxLength={200} placeholder="Why (required)" aria-label="Reason for the priority"
          onChange={(e) => setReason(e.target.value)} />
      )}{" "}
      <button className="btn" disabled={busy || (level > 0 && !reason.trim())} onClick={() => void save()}>Save</button>{" "}
      <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
      {error && <small className="error" role="alert" style={{ display: "block" }}>{error}</small>}
    </span>
  );
}

function ServiceRule({ rule, canSet, onSaved }: { rule: WaitServiceRule; canSet: boolean; onSaved: (r: WaitRules) => void }) {
  const [hours, setHours] = useState(rule.hours === null ? "" : String(rule.hours));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setHours(rule.hours === null ? "" : String(rule.hours)); }, [rule.hours]);
  const n = Number(hours);
  const valid = hours !== "" && Number.isInteger(n) && n >= 1 && n <= 168;
  async function save(value: number | null) {
    setBusy(true); setError("");
    try { onSaved(await setWaitRule(rule.id, value)); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{rule.name} {rule.hours === null ? <Pill kind="info">follows the clinic</Pill> : <Pill kind="ok">own rule</Pill>}</b>
        <small>A person has {rule.effective_hours} {rule.effective_hours === 1 ? "hour" : "hours"} to use an offered time.</small>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {canSet && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); void save(n); }}>
          <input type="number" min={1} max={168} value={hours} onChange={(e) => setHours(e.target.value)}
            aria-label={`Hours to use an offered ${rule.name} time`} placeholder="Hours" style={{ width: 110 }} />
          <button className="btn" type="submit" disabled={busy || !valid || n === rule.hours}>Save</button>
          <button className="btn ghost" type="button" disabled={busy || rule.hours === null} onClick={() => void save(null)}>Follow the clinic</button>
        </form>
      )}
    </div>
  );
}

/** Each service may give people longer or shorter to use an offered time than the clinic does. */
function ServiceRules() {
  const [rules, setRules] = useState<WaitRules | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getWaitRules().then(setRules).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load the rules"));
  }, []);
  return (
    <Card title="Time to use an offered slot, by service"
      sub={rules ? `The clinic's own rule is ${rules.clinic_hours} hours (changed under Settings). ${rules.can_set ? "A service can have its own." : "Only your clinic administrator can change this."}` : undefined}>
      {error && <div className="error" role="alert">{error}</div>}
      {rules === null ? <div className="empty">{error ? "Could not load." : "Loading…"}</div>
        : rules.services.length === 0 ? <div className="empty">No services yet.</div>
        : rules.services.map((s) => <ServiceRule key={s.id} rule={s} canSet={rules.can_set} onSaved={setRules} />)}
    </Card>
  );
}

export function WaitingList({ staff }: { staff: Staff | null }) {
  const [rows, setRows] = useState<WaitingRow[] | null>(null);
  const [asking, setAsking] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!staff) return;
    try { setRows(await listWaitlist()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the waiting list"); }
  }, [staff]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  async function remove(id: string) {
    setBusy(id); setError("");
    try { await removeFromWaitlist(id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); setAsking(""); }
  }

  if (!staff) return <NoStaff />;
  const list = rows ?? [];
  return (
    <>
      <div className="hero"><div>
        <h1>Waiting list</h1>
        <p>People waiting for a service that has no free time. When you add times, the most urgent people are told first, then the longest-waiting, one for each time. Their place is shown per service. Patients never see where you rank them.</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card title={`${list.length} waiting`} sub="A patient appears by name once their details have been entered, otherwise by id.">
          {rows === null ? <div className="empty">Loading…</div> : (
            <Table headers={["Service", "Place", "Patient", "Since", "Status", "Actions"]}
              empty="Nobody is waiting. When a service has no free times, patients can ask to be told."
              rows={list.map((e) => [
                e.service.name, `#${e.place}`,
                <Patient key="p" name={e.patient_name} id={e.patient} />,
                e.joined_at.slice(0, 10),
                <span key="s">
                  {e.state === "offered"
                    ? <Pill kind="ok">Told a time opened</Pill>
                    : <Pill kind="warn">Waiting</Pill>}
                  {e.journey && <small style={{ display: "block" }}>Journey step: {JOURNEY[e.journey]}.</small>}
                  {(e.priority ?? 0) > 0 && (
                    <small style={{ display: "block" }}>
                      <Pill kind={e.priority === 2 ? "bad" : "warn"}>{LEVELS[e.priority ?? 0]}</Pill> {e.priority_reason}
                    </small>
                  )}
                </span>,
                <span key="a">
                  <Ranking row={e} onDone={() => void load()} />{" "}
                  {asking !== e.id && (
                    <button className="btn ghost" onClick={() => setAsking(e.id)}>Remove</button>
                  )}
                  {asking === e.id && (
                    <>
                      <button className="btn" disabled={busy === e.id} onClick={() => void remove(e.id)}>Yes, remove</button>{" "}
                      <button className="btn ghost" onClick={() => setAsking("")}>Keep</button>
                    </>
                  )}
                </span>,
              ])} />
          )}
        </Card>
        <ServiceRules />
      </div>
    </>
  );
}
