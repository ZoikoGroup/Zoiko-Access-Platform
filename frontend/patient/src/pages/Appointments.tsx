import { useState } from "react";
import { cancelBooking, type Booking } from "../api";
import { dateBlock, timeLabel } from "../format";
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
  const block = dateBlock(b.local_start);
  const upcoming = b.status !== "cancelled" && b.starts_at > new Date().toISOString();

  async function cancel() {
    setBusy(true); setError("");
    try { await cancelBooking(b.id); await onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not cancel"); }
    finally { setBusy(false); setAsking(false); }
  }

  return (
    <div className="appt">
      <div className="date"><small>{block.month}</small><b>{block.day}</b><small>{block.weekday}</small></div>
      <div className="info">
        <b>{b.service.name}</b>
        <small>{b.organization.name}, {b.location.name}{b.location.address ? ` - ${b.location.address}` : ""}</small>
        <small>{timeLabel(b.local_start)} ({b.local_zone}) - {b.service.mode === "video" ? "Video" : "In person"}{b.provider ? ` - with ${b.provider.name}` : ""}</small>
        {error && <small style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      <Pill kind={STATUS[b.status].kind}>{STATUS[b.status].text}</Pill>
      {upcoming && !asking && <button className="btn ghost" onClick={() => setAsking(true)}>Cancel</button>}
      {upcoming && asking && (
        <span>
          <button className="btn" disabled={busy} onClick={() => void cancel()}>{busy ? "Cancelling…" : "Yes, cancel it"}</button>{" "}
          <button className="btn ghost" onClick={() => setAsking(false)}>Keep it</button>
        </span>
      )}
    </div>
  );
}

export function Appointments({ bookings, onChanged }: { bookings: Booking[]; onChanged: () => Promise<void> }) {
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
        <Card title={`Past and cancelled (${other.length})`}>
          {other.length === 0 ? <div className="empty">Nothing here yet.</div> : other.map((b) => <Row key={b.id} b={b} onChanged={onChanged} />)}
        </Card>
      </div>
    </>
  );
}
