import { useState } from "react";
import { ApiError, cancelBooking, downloadCalendar, getSlots, leaveWaitlist, rescheduleBooking, type Booking, type Slot, type WaitEntry } from "../api";
import { dateBlock, dayKey, dayLabel, timeLabel } from "../format";
import { Card, Pill, type PillKind } from "../ui";

const STATUS: Record<Booking["status"], { text: string; kind: PillKind }> = {
  requested: { text: "Waiting for the clinic", kind: "warn" },
  confirmed: { text: "Confirmed", kind: "ok" },
  cancelled: { text: "Cancelled", kind: "bad" },
};

function Row({ b, onChanged }: { b: Booking; onChanged: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [moving, setMoving] = useState<Slot[] | null>(null);       // free times, once "Change time" is clicked
  const block = dateBlock(b.local_start);
  const shown = b.attendance === "attended" ? { text: "Attended", kind: "ok" as PillKind }
    : b.attendance === "no_show" ? { text: "Missed", kind: "bad" as PillKind } : STATUS[b.status];
  const upcoming = b.status !== "cancelled" && b.starts_at > new Date().toISOString();

  async function cancel() {
    setBusy(true); setError("");
    try { await cancelBooking(b.id); await onChanged(); }
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
        <small>{timeLabel(b.local_start)} ({b.local_zone}) - {b.service.mode === "video" ? "Video" : "In person"}{b.provider ? ` - with ${b.provider.name}` : ""}</small>
        {b.rescheduled_from && <small>Moved from an earlier time.</small>}
        {error && <small style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      <Pill kind={shown.kind}>{shown.text}</Pill>
      {upcoming && !asking && !moving && (
        <button className="btn ghost" onClick={() => void downloadCalendar(b.id).catch(() => setError("Could not make the calendar file"))}>Add to calendar</button>
      )}
      {upcoming && !asking && !moving && <button className="btn ghost" onClick={() => void openTimes()}>Change time</button>}
      {upcoming && !asking && <button className="btn ghost" onClick={() => setAsking(true)}>Cancel</button>}
      {upcoming && asking && (
        <span>
          <button className="btn" disabled={busy} onClick={() => void cancel()}>{busy ? "Cancelling…" : "Yes, cancel it"}</button>{" "}
          <button className="btn ghost" onClick={() => setAsking(false)}>Keep it</button>
        </span>
      )}
      {moving && (
        <div style={{ flexBasis: "100%" }}>
          <b>Pick a new time</b> <small>(the clinic will confirm it again)</small>
          {moving.length === 0 ? <div className="empty">There are no other free times for this service right now.</div> : (
            [...byDay.entries()].map(([day, list]) => (
              <div key={day}>
                <div className="slot-day">{dayLabel(list[0].local_start)} ({list[0].local_zone})</div>
                <div className="slots">
                  {list.map((s) => (
                    <button key={s.id} className="slot" disabled={busy} onClick={() => void moveTo(s)}>
                      {timeLabel(s.local_start)}{s.provider ? <small style={{ display: "block", fontWeight: 400 }}>{s.provider.name}</small> : null}
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
  const upcoming = bookings.filter((b) => b.status !== "cancelled" && b.starts_at > now);
  const other = bookings.filter((b) => !upcoming.includes(b)).reverse();
  return (
    <>
      <div className="hero"><div><h1>Appointments</h1><p>Your bookings and their status. A clinic must confirm a new booking.</p></div></div>
      <div className="grid">
        <Card title={`Upcoming (${upcoming.length})`}>
          {upcoming.length === 0 ? <div className="empty">Nothing upcoming.</div> : upcoming.map((b) => <Row key={b.id} b={b} onChanged={onChanged} />)}
        </Card>
        {waiting.length > 0 && (
          <Card title={`Waiting list (${waiting.length})`} sub="Services with no free time that you asked to hear about.">
            {waiting.map((w) => <WaitRow key={w.id} w={w} onChanged={onChanged} />)}
          </Card>
        )}
        <Card title={`Past and cancelled (${other.length})`}>
          {other.length === 0 ? <div className="empty">Nothing here yet.</div> : other.map((b) => <Row key={b.id} b={b} onChanged={onChanged} />)}
        </Card>
      </div>
    </>
  );
}
