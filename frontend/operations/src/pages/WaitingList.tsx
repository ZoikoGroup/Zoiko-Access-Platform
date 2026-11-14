import { useCallback, useEffect, useState } from "react";
import { listWaitlist, removeFromWaitlist, type Staff } from "../api";
import type { WaitingRow } from "../types";
import { Card, Patient, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

/** Who is waiting for each service, longest first. Patients appear as ids (names are not stored). */
const JOURNEY: Record<string, string> = {
  waiting: "waiting for a free time", offered: "told a time opened, waiting for the patient",
  booked: "booked", left: "left the list",
};

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
        <p>People waiting for a service that has no free time. When you add times, the longest-waiting people are told, one for each time. Their place is shown per service.</p>
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
                </span>,
                <span key="a">
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
