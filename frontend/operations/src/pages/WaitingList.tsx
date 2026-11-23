import { useCallback, useEffect, useState } from "react";
import { listWaitlist, removeFromWaitlist, setWaitlistPriority, type Staff } from "../api";
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
      </div>
    </>
  );
}
