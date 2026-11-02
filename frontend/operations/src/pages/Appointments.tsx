import { useCallback, useEffect, useState } from "react";
import { cancelBooking, confirmBooking, listBookings, listFreeTimes, markAttendance, rescheduleBooking, type Staff } from "../api";
import type { BookingRow, FreeTime } from "../types";
import { Card, Patient, Pill, Table, shortId, type PillKind } from "../ui";

const STATUS: Record<BookingRow["status"], { text: string; kind: PillKind }> = {
  requested: { text: "Needs confirmation", kind: "warn" },
  confirmed: { text: "Confirmed", kind: "ok" },
  cancelled: { text: "Cancelled", kind: "bad" },
};

function when(local: string, zone: string): string {
  const [d, t = ""] = local.split("T");
  return `${d} ${t.slice(0, 5)} (${zone})`;
}

export function Appointments({ staff }: { staff: Staff | null }) {
  const [rows, setRows] = useState<BookingRow[] | null>(null);
  const [filter, setFilter] = useState<"all" | BookingRow["status"]>("all");
  const [busy, setBusy] = useState("");
  const [asking, setAsking] = useState("");
  const [error, setError] = useState("");
  const [moving, setMoving] = useState<{ booking: BookingRow; times: FreeTime[] } | null>(null);

  const load = useCallback(async () => {
    if (!staff) return;
    try { setRows(await listBookings()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load bookings"); }
  }, [staff]);

  useEffect(() => {
    setRows(null);
    void load();
    const timer = setInterval(() => void load(), 10_000);
    return () => clearInterval(timer);
  }, [load]);

  async function run(id: string, work: (id: string) => Promise<unknown>) {
    setBusy(id); setError("");
    try { await work(id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); setAsking(""); }
  }

  async function openMove(b: BookingRow) {
    setError("");
    try {
      const times = (await listFreeTimes(b.service.id)).filter((t) => t.starts_at !== b.starts_at);
      setMoving({ booking: b, times });
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load free times"); }
  }

  async function moveTo(t: FreeTime) {
    if (!moving) return;
    setBusy(moving.booking.id); setError("");
    try { await rescheduleBooking(moving.booking.id, t.id); setMoving(null); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That time could not be used. Pick another."); await openMove(moving.booking); }
    finally { setBusy(""); }
  }

  if (!staff) return <NoStaff />;
  const shown = (rows ?? []).filter((b) => filter === "all" || b.status === filter);
  const waiting = (rows ?? []).filter((b) => b.status === "requested").length;

  return (
    <>
      <div className="hero">
        <div>
          <h1>Appointments</h1>
          <p>Bookings for {staff.organizationName}. Patients request; staff confirm. {waiting > 0 && <b>{waiting} waiting for confirmation.</b>}</p>
        </div>
        <span className="spacer" />
        <label className="chip">Show
          <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} aria-label="Filter by status">
            <option value="all">all</option>
            <option value="requested">needs confirmation</option>
            <option value="confirmed">confirmed</option>
            <option value="cancelled">cancelled</option>
          </select>
        </label>
      </div>
      {error && <div className="error">{error}</div>}
      {moving && (
        <div className="grid">
          <Card title={`Move ${moving.booking.service.name} (${moving.booking.patient_name ?? shortId(moving.booking.person)})`}
            sub={`Now ${when(moving.booking.local_start, moving.booking.local_zone)}. A confirmed appointment stays confirmed; a waiting one stays waiting.`}>
            {moving.times.length === 0 ? <div className="empty">No other free times for this service.</div> : (
              <div className="row" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {moving.times.map((t) => (
                  <button key={t.id} className="btn ghost" disabled={busy === moving.booking.id} onClick={() => void moveTo(t)}>
                    {when(t.local_start, t.local_zone)}{t.provider ? ` - ${t.provider.name}` : ""}
                  </button>
                ))}
              </div>
            )}
            <div style={{ marginTop: 12 }}><button className="btn ghost" onClick={() => setMoving(null)}>Close</button></div>
          </Card>
        </div>
      )}
      <div className="grid">
        <Card title={`${shown.length} booking${shown.length === 1 ? "" : "s"}`} sub="Patients appear as ids: names are not stored yet (DEV-024).">
          {rows === null ? <div className="empty">Loading…</div> : (
            <Table headers={["When (clinic time)", "Service", "Location", "Patient", "Status", "Actions"]}
              empty="No bookings match."
              rows={shown.map((b) => [
                when(b.local_start, b.local_zone), b.service.name, b.location.name,
                <Patient key="p" name={b.patient_name} id={b.person} />,
                b.attendance === "attended" ? <Pill key="s" kind="ok">Attended</Pill>
                  : b.attendance === "no_show" ? <Pill key="s" kind="bad">Did not attend</Pill>
                  : b.late ? <Pill key="s" kind="bad">Late: needs confirmation</Pill>
                  : <Pill key="s" kind={STATUS[b.status].kind}>{STATUS[b.status].text}</Pill>,
                <span key="a">
                  {b.status === "confirmed" && b.starts_at <= new Date().toISOString() && (
                    <>
                      {b.attendance !== "attended" && (
                        <button className="btn" disabled={busy === b.id}
                          onClick={() => void run(b.id, (id) => markAttendance(id, "attended"))}>Attended</button>
                      )}{" "}
                      {b.attendance !== "no_show" && (
                        <button className="btn ghost" disabled={busy === b.id}
                          onClick={() => void run(b.id, (id) => markAttendance(id, "no_show"))}>Did not come</button>
                      )}{" "}
                    </>
                  )}
                  {b.status === "requested" && (
                    <button className="btn" disabled={busy === b.id} onClick={() => void run(b.id, confirmBooking)}>Confirm</button>
                  )}{" "}
                  {b.status !== "cancelled" && asking !== b.id && (
                    <>
                      {b.starts_at > new Date().toISOString() && (
                        <><button className="btn ghost" onClick={() => void openMove(b)}>Move</button>{" "}</>
                      )}
                      {!b.attendance && (
                        <button className="btn ghost" onClick={() => setAsking(b.id)}>Cancel</button>
                      )}
                    </>
                  )}
                  {asking === b.id && (
                    <>
                      <button className="btn" disabled={busy === b.id} onClick={() => void run(b.id, cancelBooking)}>Yes, cancel</button>{" "}
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

export function NoStaff() {
  return (
    <div className="grid">
      <Card title="Your account cannot manage this">
        <div className="empty">
          Services and appointments are managed by clinic operations staff and administrators. Your account does not
          hold that role at an organization, so there is nothing for you to manage here. Ask an administrator,
          or sign in with a clinic-operations account.
        </div>
      </Card>
    </div>
  );
}
