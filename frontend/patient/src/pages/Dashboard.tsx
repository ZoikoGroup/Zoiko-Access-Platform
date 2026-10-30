import { useEffect, useState } from "react";
import { getServices, type Booking, type Me, type Notice, type Service } from "../api";
import type { PageId } from "../App";
import { dateBlock, timeLabel, whenLabel } from "../format";
import { Card, Icon, Pill, type PillKind } from "../ui";

const SOON = [
  { title: "My Health Overview", note: "Vitals and health records need the clinical record service (a later phase)." },
  { title: "Referral Status", note: "Arrives with the workflow engine (backend step B8)." },
  { title: "Bills & Payments", note: "A later commercial phase." },
];

const STATUS: Record<Booking["status"], { text: string; kind: PillKind }> = {
  requested: { text: "Waiting for the clinic", kind: "warn" },
  confirmed: { text: "Confirmed", kind: "ok" },
  cancelled: { text: "Cancelled", kind: "bad" },
};

export function Dashboard({ name, me, bookings, notices, goTo }: {
  name: string; me: Me; bookings: Booking[]; notices: Notice[]; goTo: (p: PageId) => void;
}) {
  const [services, setServices] = useState<Service[]>([]);
  useEffect(() => { getServices().then(setServices).catch(() => setServices([])); }, []);

  const now = new Date().toISOString();
  const upcoming = bookings
    .filter((b) => b.status !== "cancelled" && b.starts_at > now)
    .sort((a, b) => a.starts_at.localeCompare(b.starts_at))
    .slice(0, 3);
  const recent = [...bookings].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 4);

  return (
    <>
      <div className="hero banner">
        <div>
          <h1>Welcome back, {name}!</h1>
          <p>Your health and care, all in one place with Zoiko Access.</p>
        </div>
        <span className="spacer" />
        <Pill kind="info">{me.persons.length > 0 ? "Patient record linked" : "No patient record linked yet"}</Pill>
      </div>

      <div className="grid tiles">
        <button className="tile" onClick={() => goTo("find")}>
          <span className="icon"><Icon name="search" size={22} /></span>
          <span><b>Find Healthcare</b><small>Search services and clinics</small></span>
        </button>
        <button className="tile" onClick={() => goTo("find")}>
          <span className="icon"><Icon name="appointments" size={22} /></span>
          <span><b>Book Appointment</b><small>Schedule an in-person or video visit</small></span>
        </button>
        {[["My Health Records", "services", "View and download your records"], ["My Referrals", "referrals", "Track your specialist referrals"]].map(([label, icon, note]) => (
          <button key={label} className="tile" disabled title="Not built yet">
            <span className="icon"><Icon name={icon} size={22} /></span>
            <span><b>{label}</b><small>{note} - coming soon</small></span>
          </button>
        ))}
      </div>

      <div className="grid cols-2">
        <Card title="Upcoming Appointments" action={<button className="btn ghost" onClick={() => goTo("appointments")}>View all</button>}>
          {upcoming.length === 0 ? (
            <div className="empty">
              No upcoming appointments. <button className="btn" onClick={() => goTo("find")}>Find care</button>
            </div>
          ) : upcoming.map((b) => {
            const d = dateBlock(b.local_start);
            return (
              <div key={b.id} className="appt">
                <div className="date"><small>{d.month}</small><b>{d.day}</b><small>{d.weekday}</small></div>
                <div className="info">
                  <b>{b.service.name}</b>
                  <small>{b.organization.name}, {b.location.name}</small>
                  <small>{timeLabel(b.local_start)} ({b.local_zone}) - {b.service.mode === "video" ? "Video" : "In person"}{b.provider ? ` - with ${b.provider.name}` : ""}</small>
                </div>
                <Pill kind={STATUS[b.status].kind}>{STATUS[b.status].text}</Pill>
              </div>
            );
          })}
        </Card>

        <Card title="My Health Overview" className="planned" action={<Pill kind="warn">not built yet</Pill>}>
          <div className="empty">{SOON[0].note}</div>
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Recommended for You" sub="Services you can book now, from the clinics' live catalog"
          action={<button className="btn ghost" onClick={() => goTo("find")}>View all</button>}>
          {services.length === 0 ? <div className="empty">No services are available yet.</div> : (
            <div className="service-grid">
              {services.slice(0, 4).map((s) => (
                <div key={s.id} className="service">
                  <h3>{s.name}</h3>
                  <span className="meta">{s.organization.name} - {s.duration_minutes} min</span>
                  <span className="next">
                    {s.next_slot_local && s.next_slot_zone
                      ? <>Next: <b>{whenLabel(s.next_slot_local, s.next_slot_zone)}</b></>
                      : <Pill kind="warn">No free times</Pill>}
                  </span>
                  <button className="btn" onClick={() => goTo("find")}>Book Appointment</button>
                </div>
              ))}
            </div>
          )}
        </Card>

        <div className="grid" style={{ margin: 0 }}>
          {SOON.slice(1, 3).map((c) => (
            <Card key={c.title} className="planned" title={c.title} action={<Pill kind="warn">not built yet</Pill>}>
              <div className="empty">{c.note}</div>
            </Card>
          ))}
        </div>
      </div>

      <div className="grid cols-2">
        <Card title="Recent Notifications" sub="Updates about your appointments"
          action={<button className="btn ghost" onClick={() => goTo("notifications")}>View all</button>}>
          {notices.length === 0 ? <div className="empty">No notifications yet.</div> : notices.slice(0, 3).map((n) => (
            <div key={n.id} className="appt" style={{ padding: "8px 0" }}>
              <div className="info">
                <b>{!n.read && <span className="unread-dot" aria-label="unread" />}{n.title}</b>
                <small>{n.body}</small>
              </div>
              <small style={{ color: "var(--muted)" }}>{n.created_at.slice(0, 10)}</small>
            </div>
          ))}
        </Card>
        <Card title="Recent Activity" sub="Your latest bookings" action={<button className="btn ghost" onClick={() => goTo("appointments")}>View all</button>}>
          {recent.length === 0 ? <div className="empty">Nothing yet.</div> : recent.map((b) => (
            <div key={b.id} className="appt" style={{ padding: "8px 0" }}>
              <div className="info">
                <b>Appointment {b.status === "cancelled" ? "cancelled" : "booked"}</b>
                <small>{b.service.name} - {b.organization.name}</small>
              </div>
              <small style={{ color: "var(--muted)" }}>{b.created_at.slice(0, 10)}</small>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
