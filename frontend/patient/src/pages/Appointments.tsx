import { useCallback, useState } from "react";
import { BookingHistory } from "@shared/BookingHistory";
import { PATIENT_REASONS } from "@shared/cancelReasons";
import { ApiError, cancelBooking, downloadCalendar, getBookingHistory, getSlots, leaveWaitlist, rescheduleBooking, type Booking, type Slot, type WaitEntry } from "../api";
import { dateBlock, dayKey, dayLabel, timeLabel } from "../format";
import { Card, Pill, VerifiedMark, type PillKind } from "../ui";

const STATUS: Record<Booking["status"], { text: string; kind: PillKind }> = {
  requested: { text: "Waiting for the clinic", kind: "warn" },
  confirmed: { text: "Confirmed", kind: "ok" },
  cancelled: { text: "Cancelled", kind: "bad" },
};

function Row({ b, onChanged, earlierStays }: { b: Booking; onChanged: () => Promise<void>; earlierStays: boolean }) {
  const [asking, setAsking] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const loadHistory = useCallback(() => getBookingHistory(b.id), [b.id]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [why, setWhy] = useState("");                              // optional reason for cancelling
  const [moving, setMoving] = useState<Slot[] | null>(null);       // free times, once "Change time" is clicked
  const block = dateBlock(b.local_start);
  const shown = b.attendance === "attended" ? { text: "Attended", kind: "ok" as PillKind }
    : b.attendance === "no_show" ? { text: "Missed", kind: "bad" as PillKind }
    : b.pending_change === "cancel" ? { text: "Cancel asked: waiting for the clinic", kind: "warn" as PillKind }
    : b.pending_change === "rebook" ? { text: "New time asked: waiting for the clinic", kind: "warn" as PillKind }
    : STATUS[b.status];
  const upcoming = b.status !== "cancelled" && b.starts_at > new Date().toISOString();
  const waiting = b.pending_change !== null;                      // the clinic has a change to answer

  async function cancel() {
    setBusy(true); setError("");
    try { await cancelBooking(b.id, why); await onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not cancel"); }
    finally { setBusy(false); setAsking(false); }
  }

  async function openTimes() {
    setError("");
    try { setMoving((await getSlots(b.service.id)).filter((s) => s.starts_at !== b.starts_at)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load times"); }
  }

  async function moveTo(slot: Slot) {
    setBusy(true); setError("");
    try { await rescheduleBooking(b.id, slot.id); setMoving(null); await onChanged(); }
    catch (e) {
      setError(e instanceof ApiError && e.status === 409
        ? "That time was just taken, or this appointment can no longer be moved. Pick another."
        : e instanceof Error ? e.message : "Could not move the appointment");
      if (e instanceof ApiError && e.status === 409) await openTimes();
    } finally { setBusy(false); }
  }

  const byDay = new Map<string, Slot[]>();
  for (const s of moving ?? []) byDay.set(dayKey(s.local_start), [...(byDay.get(dayKey(s.local_start)) ?? []), s]);

  return (
    <div className="appt">
      <div className="date"><small>{block.month}</small><b>{block.day}</b><small>{block.weekday}</small></div>
      <div className="info">
        <b>{b.service.name}</b>
        <small>{b.organization.name}, {b.location.name}{b.location.address ? ` - ${b.location.address}` : ""}</small>
        <small>{timeLabel(b.local_start)} ({b.local_zone}) - {b.service.mode === "video" ? "Video" : "In person"}{b.provider ? ` - with ${b.provider.name} ` : ""}{b.provider && <VerifiedMark on={b.provider.verified} />}</small>
        {b.rescheduled_from && (b.status === "requested" && earlierStays
          ? <small>You asked to move here from an earlier time. The earlier time stays until the clinic confirms this one.</small>
          : <small>Moved from an earlier time.</small>)}
        {b.pending_change === "cancel" && <small>You asked to cancel. This appointment stays until the clinic confirms the cancellation.</small>}
        {b.pending_change === "rebook" && <small>You asked for a new time. This appointment stays until the clinic confirms the new one.</small>}
        {error && <small style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      <Pill kind={shown.kind}>{shown.text}</Pill>
      <button className="btn ghost" onClick={() => setShowHistory(!showHistory)}>{showHistory ? "Hide history" : "History"}</button>
      {upcoming && !asking && !moving && (
        <button className="btn ghost" onClick={() => void downloadCalendar(b.id).catch(() => setError("Could not make the calendar file"))}>Add to calendar</button>
      )}
      {upcoming && !waiting && !asking && !moving && <button className="btn ghost" onClick={() => void openTimes()}>Change time</button>}
      {upcoming && !waiting && !asking && <button className="btn ghost" onClick={() => setAsking(true)}>{b.status === "requested" ? "Cancel" : "Ask to cancel"}</button>}
      {upcoming && !waiting && asking && (
        <span>
          {b.status === "confirmed" && <small style={{ display: "block" }}>The clinic must confirm the cancellation. Until then this appointment stays.</small>}
          <select aria-label="Why are you cancelling? (optional)" value={why} onChange={(e) => setWhy(e.target.value)}>
            <option value="">Why? (optional)</option>
            {Object.entries(PATIENT_REASONS).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
          </select>{" "}
          <button className="btn" disabled={busy} onClick={() => void cancel()}>{busy ? "Sending…" : b.status === "confirmed" ? "Yes, ask to cancel" : "Yes, cancel it"}</button>{" "}
          <button className="btn ghost" onClick={() => setAsking(false)}>Keep it</button>
        </span>
      )}
      {showHistory && <div style={{ flexBasis: "100%" }}><BookingHistory load={loadHistory} /></div>}
      {moving && (
        <div style={{ flexBasis: "100%" }}>
          <b>Pick a new time</b> <small>(the clinic must confirm the new time; your current time stays until it does)</small>
          {moving.length === 0 ? <div className="empty">There are no other free times for this service right now.</div> : (
            [...byDay.entries()].map(([day, list]) => (
              <div key={day}>
                <div className="slot-day">{dayLabel(list[0].local_start)} ({list[0].local_zone})</div>
                <div className="slots">
                  {list.map((s) => (
                    <button key={s.id} className="slot" disabled={busy} onClick={() => void moveTo(s)}>
                      {timeLabel(s.local_start)}{s.provider ? <small style={{ display: "block", fontWeight: 400 }}>{s.provider.name} <VerifiedMark on={s.provider.verified} /></small> : null}
                    </button>
                  ))}
                </div>
              </div>
            ))
          )}
          <button className="btn ghost" onClick={() => setMoving(null)}>Keep my current time</button>
        </div>
      )}
    </div>
  );
}

function WaitRow({ w, onChanged }: { w: WaitEntry; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  async function leave() {
    setBusy(true);
    try { await leaveWaitlist(w.id); await onChanged(); } finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{w.service.name}</b>
        <small>{w.organization.name} - on the list since {w.joined_at.slice(0, 10)}</small>
        <small>{w.state === "offered"
          ? "A time opened up. Book it under Find Healthcare before it is taken."
          : "We will tell you when a time opens up."}</small>
      </div>
      <Pill kind={w.state === "offered" ? "ok" : "warn"}>{w.state === "offered" ? "Time open" : "Waiting"}</Pill>
      <button className="btn ghost" disabled={busy} onClick={() => void leave()}>Leave the list</button>
    </div>
  );
}

export function Appointments({ bookings, waiting, onChanged }: {
  bookings: Booking[]; waiting: WaitEntry[]; onChanged: () => Promise<void>;
}) {
  const now = new Date().toISOString();
  // True while the earlier appointment this one would replace is still standing.
  const stays = (b: Booking) => !!b.rescheduled_from && bookings.some((x) => x.id === b.rescheduled_from && x.status !== "cancelled");
  const upcoming = bookings.filter((b) => b.status !== "cancelled" && b.starts_at > now);
  const other = bookings.filter((b) => !upcoming.includes(b)).reverse();
  return (
    <>
      <div className="hero"><div><h1>Appointments</h1><p>Your bookings and their status. A clinic must confirm a new booking.</p></div></div>
      <div className="grid">
        <Card title={`Upcoming (${upcoming.length})`}>
          {upcoming.length === 0 ? <div className="empty">Nothing upcoming.</div> : upcoming.map((b) => <Row key={b.id} b={b} onChanged={onChanged} earlierStays={stays(b)} />)}
        </Card>
        {waiting.length > 0 && (
          <Card title={`Waiting list (${waiting.length})`} sub="Services with no free time that you asked to hear about.">
            {waiting.map((w) => <WaitRow key={w.id} w={w} onChanged={onChanged} />)}
          </Card>
        )}
        <Card title={`Past and cancelled (${other.length})`}>
          {other.length === 0 ? <div className="empty">Nothing here yet.</div> : other.map((b) => <Row key={b.id} b={b} onChanged={onChanged} earlierStays={stays(b)} />)}
        </Card>
      </div>
    </>
  );
}
