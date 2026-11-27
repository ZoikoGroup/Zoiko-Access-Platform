import { useCallback, useEffect, useState } from "react";
import { listWaitlist, removeFromWaitlist, setWaitlistPriority, type Staff } from "../api";
import type { WaitingRow } from "../types";
import { Card, Patient, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

const LEVELS: Record<number, string> = { 0: "Routine", 1: "Priority", 2: "Urgent" };
/** Why someone is above routine: a code from this list, never free text (what staff would type is
 * health information). Must match PriorityReason in backend/modules/waitlist/models.py (DEV-366). */
const REASONS: Record<string, string> = {
  clinical_urgency: "Clinical urgency, as judged by a clinician",
  clinic_cancelled: "The clinic cancelled their earlier appointment",
  long_wait: "Waited much longer than usual",
  access_needs: "Needs a particular time for access or care reasons",
  other: "Another reason, recorded in the patient's notes",
};

/** Staff move someone up the queue. A level above routine needs a reason from the list. */
function Ranking({ row, onDone }: { row: WaitingRow; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<0 | 1 | 2>(row.priority ?? 0);
  const [reason, setReason] = useState(row.priority_reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true); setError("");
    try { await setWaitlistPriority(row.id, level, level > 0 ? reason : ""); setOpen(false); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  if (!open) return <button className="btn ghost" onClick={() => setOpen(true)}>Set priority<span className="sr-only"> ({row.service.name})</span></button>;
  return (
    <span>
      <select value={level} onChange={(e) => setLevel(Number(e.target.value) as 0 | 1 | 2)} aria-label="Priority">
        {[0, 1, 2].map((n) => <option key={n} value={n}>{LEVELS[n]}</option>)}
      </select>{" "}
      {level > 0 && (
        <select value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason for the priority">
          <option value="">Choose a reason…</option>
          {Object.entries(REASONS).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
        </select>
      )}{" "}
      <button className="btn" disabled={busy || (level > 0 && !reason)} onClick={() => void save()}>Save</button>{" "}
      <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
      {error && <small className="error" role="alert" style={{ display: "block" }}>{error}</small>}
    </span>
  );
}

/** Who is waiting for each service, most urgent then longest first. Patients appear as ids (names are not stored). */
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
                <span key="p">
                  <Patient name={e.patient_name} id={e.patient} />
                  {(e.priority ?? 0) > 0 && (
                    <small style={{ display: "block" }}>
                      <Pill kind={e.priority === 2 ? "bad" : "warn"}>{LEVELS[e.priority ?? 0]}</Pill> {REASONS[e.priority_reason ?? ""] ?? ""}
                    </small>
                  )}
                </span>,
                e.joined_at.slice(0, 10),
                e.state === "offered"
                  ? <Pill key="s" kind="ok">Told a time opened</Pill>
                  : <Pill key="s" kind="warn">Waiting</Pill>,
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
      </div>
    </>
  );
}
