import { useCallback, useEffect, useState } from "react";
import { BookingHistory } from "@shared/BookingHistory";
import { CLINIC_REASONS, reasonText } from "@shared/cancelReasons";
import { cancelBooking, confirmBooking, getBookingHistory, getBookingSystem, listBookings, listFreeTimes, markAttendance, rescheduleBooking, type Staff } from "../api";
import type { BookingRow, FreeTime } from "../types";
import { Card, Patient, Pill, Table, shortId, type PillKind } from "../ui";

const STATUS: Record<BookingRow["status"], { text: string; kind: PillKind }> = {
  requested: { text: "Needs confirmation", kind: "warn" },
  confirmed: { text: "Confirmed", kind: "ok" },
  cancelled: { text: "Cancelled", kind: "bad" },
};

/** What a booking waiting on the clinic's booking system should say: a receipt is not a confirmation. */
function providerWords(stage: { stage: string; operation: string }): { text: string; kind: PillKind; note: string } | null {
  if (stage.stage !== "awaiting_provider") return null;
  switch (stage.operation) {
    case "accepted":
      return { text: "Received, not confirmed", kind: "warn", note: "The booking system has the request but has not confirmed it yet. It is not confirmed until the system says so." };
    case "unknown":
      return { text: "No answer from the booking system", kind: "bad", note: "We do not know whether it was confirmed. See Exceptions: a person must decide." };
    case "dead":
      return { text: "Booking system did not take it", kind: "bad", note: "It kept failing. See Exceptions to try again." };
    default:
      return { text: "Asked the booking system", kind: "info", note: "Waiting for the booking system's own answer." };
  }
}

function when(local: string, zone: string): string {
  const [d, t = ""] = local.split("T");
  return `${d} ${t.slice(0, 5)} (${zone})`;
}

export function Appointments({ staff }: { staff: Staff | null }) {
  const [rows, setRows] = useState<BookingRow[] | null>(null);
  const [filter, setFilter] = useState<"all" | BookingRow["status"]>("all");
  const [busy, setBusy] = useState("");
  const [asking, setAsking] = useState("");
  const [historyFor, setHistoryFor] = useState("");       // the appointment whose steps are shown
  const loadHistory = useCallback(() => getBookingHistory(historyFor), [historyFor]);
  const [why, setWhy] = useState("");                       // optional reason for cancelling
  const [bulk, setBulk] = useState<"" | "asking" | "working">("");   // "Confirm all waiting"
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [moving, setMoving] = useState<{ booking: BookingRow; times: FreeTime[] } | null>(null);
  const [system, setSystem] = useState("");               // the booking system that confirms for this clinic ("" = by hand)
  useEffect(() => { getBookingSystem().then((s) => setSystem(s.system)).catch(() => setSystem("")); }, []);

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
    setBusy(id); setError(""); setWhy("");
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
  // Waiting bookings whose time has not passed: the only ones "Confirm all" touches.
  // (With a booking system the bookings are asked about one at a time, so "Confirm all" is not offered.)
  const confirmable = system ? [] : (rows ?? []).filter((b) => b.status === "requested" && b.starts_at > new Date().toISOString());

  async function confirmAll() {
    setBulk("working"); setError(""); setNote("");
    let done = 0;
    let failed = 0;
    for (const b of confirmable) {
      try { await confirmBooking(b.id); done += 1; } catch { failed += 1; }
    }
    await load();
    setNote(failed === 0 ? `Confirmed ${done} appointment${done === 1 ? "" : "s"}.`
      : `Confirmed ${done}; ${failed} could not be confirmed (someone may have changed them). They are still waiting.`);
    setBulk("");
  }
  const shown = (rows ?? []).filter((b) => filter === "all" || b.status === filter);
  const waiting = (rows ?? []).filter((b) => b.status === "requested").length;

  return (
    <>
      <div className="hero">
        <div>
          <h1>Appointments</h1>
          <p>Bookings for {staff.organizationName}. {system ? "Patients request; the booking system confirms." : "Patients request; staff confirm."} {waiting > 0 && <b>{waiting} waiting for confirmation.</b>}</p>
        </div>
        <span className="spacer" />
        {confirmable.length > 1 && bulk === "" && (
          <button className="btn" onClick={() => setBulk("asking")}>Confirm all waiting ({confirmable.length})</button>
        )}
        {bulk === "asking" && (
          <span>
            Confirm {confirmable.length} appointments?{" "}
            <button className="btn" onClick={() => void confirmAll()}>Yes, confirm all</button>{" "}
            <button className="btn ghost" onClick={() => setBulk("")}>No</button>
          </span>
        )}
        {bulk === "working" && <span>Confirming…</span>}
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
      {note && <div className="notice" role="status" style={{ background: "var(--ok-bg)", color: "var(--ok)" }}>{note}</div>}
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
        <Card title={`${shown.length} booking${shown.length === 1 ? "" : "s"}`}>
          {rows === null ? <div className="empty">Loading…</div> : (
            <Table headers={["When (clinic time)", "Service", "Location", "Patient", "Status", "Actions"]}
              empty="No bookings match."
              rows={shown.map((b) => [
                when(b.local_start, b.local_zone), b.service.name, b.location.name,
                <Patient key="p" name={b.patient_name} id={b.person} />,
                b.attendance === "attended" ? <Pill key="s" kind="ok">Attended</Pill>
                  : b.attendance === "no_show" ? <Pill key="s" kind="bad">Did not attend</Pill>
                  : b.late ? <Pill key="s" kind="bad">Late: needs confirmation</Pill>
                  : b.pending_change === "cancel" ? (
                    <span key="s"><Pill kind="warn">Patient asked to cancel</Pill>
                      <small style={{ display: "block" }}>It stays booked until you confirm the cancellation.{b.cancel_reason ? ` Reason: ${reasonText(b.cancel_reason)}` : ""}</small></span>)
                  : b.pending_change === "rebook" ? (
                    <span key="s"><Pill kind="warn">Patient asked for a new time</Pill>
                      <small style={{ display: "block" }}>This time stays until you confirm the new request.</small></span>)
                  : b.status === "requested" && b.provider_stage && providerWords(b.provider_stage) ? (
                    <span key="s"><Pill kind={providerWords(b.provider_stage)!.kind}>{providerWords(b.provider_stage)!.text}</Pill>
                      <small style={{ display: "block" }}>{providerWords(b.provider_stage)!.note}</small></span>)
                  : b.status === "requested" && b.rescheduled_from ? (
                    <span key="s"><Pill kind={STATUS[b.status].kind}>{STATUS[b.status].text}</Pill>
                      <small style={{ display: "block" }}>A move request: confirming it releases the earlier time.</small></span>)
                  : <span key="s"><Pill kind={STATUS[b.status].kind}>{STATUS[b.status].text}</Pill>{b.status === "cancelled" && b.cancel_reason && <small style={{ display: "block" }}>{reasonText(b.cancel_reason)}</small>}</span>,
                <span key="a">
                  <button className="btn ghost" onClick={() => setHistoryFor(historyFor === b.id ? "" : b.id)}>{historyFor === b.id ? "Hide history" : "History"}</button>{" "}
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
                    <button className="btn" disabled={busy === b.id || (b.provider_stage?.stage === "awaiting_provider" && ["", "pending", "sent", "accepted"].includes(b.provider_stage.operation))}
                      onClick={() => void run(b.id, confirmBooking)}>{system ? "Ask booking system" : "Confirm"}</button>
                  )}{" "}
                  {b.status !== "cancelled" && asking !== b.id && (
                    <>
                      {b.starts_at > new Date().toISOString() && (
                        <><button className="btn ghost" onClick={() => void openMove(b)}>Move</button>{" "}</>
                      )}
                      {!b.attendance && (
                        <button className="btn ghost" onClick={() => setAsking(b.id)}>{b.pending_change === "cancel" ? "Confirm the cancellation" : "Cancel"}</button>
                      )}
                    </>
                  )}
                  {asking === b.id && (
                    <>
                      <select aria-label="Why is it cancelled? (optional)" value={why} onChange={(e) => setWhy(e.target.value)}>
                        <option value="">Why? (optional)</option>
                        {Object.entries(CLINIC_REASONS).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
                      </select>{" "}
                      <button className="btn" disabled={busy === b.id} onClick={() => void run(b.id, (id) => cancelBooking(id, why))}>{b.pending_change === "cancel" ? "Yes, confirm the cancellation" : "Yes, cancel"}</button>{" "}
                      <button className="btn ghost" onClick={() => setAsking("")}>Keep</button>
                    </>
                  )}
                </span>,
              ])} />
          )}
        </Card>
      </div>
      {historyFor && (
        <div className="grid">
          <Card title="History of this appointment" sub="Every step, oldest first. Patients appear only as the patient or family."
            action={<button className="btn ghost" onClick={() => setHistoryFor("")}>Close</button>}>
            <BookingHistory key={historyFor} load={loadHistory} />
          </Card>
        </div>
      )}
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
