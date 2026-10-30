import { getSchedule } from "../api";
import type { Appointment } from "../types";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, shortId } from "../ui";

function parts(local: string): Date {
  const [d, t = "00:00:00"] = local.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [h, min] = t.split(":").map(Number);
  return new Date(y, m - 1, day, h, min);
}
const dayLabel = (local: string) =>
  parts(local).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const timeLabel = (local: string) =>
  parts(local).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

export function Schedule() {
  const result = useLoad(getSchedule);
  return (
    <Gate result={result}>
      {(rows) => {
        const now = new Date().toISOString();
        const upcoming = rows.filter((a) => a.ends_at > now);
        const past = rows.filter((a) => a.ends_at <= now).reverse();
        const byDay = new Map<string, Appointment[]>();
        for (const a of upcoming) byDay.set(a.local_start.slice(0, 10), [...(byDay.get(a.local_start.slice(0, 10)) ?? []), a]);
        return (
          <>
            <div className="hero"><div>
              <h1>My schedule</h1>
              <p>Your confirmed appointments. A patient appears here once the clinic confirms the booking. Patients show as ids: names are not stored yet.</p>
            </div></div>
            <div className="grid">
              <Card title={`Upcoming (${upcoming.length})`}>
                {upcoming.length === 0 ? <div className="empty">Nothing booked with you yet.</div> : [...byDay.entries()].map(([day, list]) => (
                  <div key={day}>
                    <div className="slot-day">{dayLabel(list[0].local_start)}</div>
                    {list.map((a) => <Row key={a.id} a={a} />)}
                  </div>
                ))}
              </Card>
              <Card title={`Earlier (${past.length})`}>
                {past.length === 0 ? <div className="empty">Nothing yet.</div> : past.map((a) => <Row key={a.id} a={a} />)}
              </Card>
            </div>
          </>
        );
      }}
    </Gate>
  );
}

function Row({ a }: { a: Appointment }) {
  return (
    <div className="appt">
      <div className="date"><b style={{ fontSize: 16 }}>{timeLabel(a.local_start)}</b><small>{a.local_zone.split("/").pop()}</small></div>
      <div className="info">
        <b>{a.service.name}</b>
        <small>{a.organization.name}, {a.location.name} - {a.service.mode === "video" ? "Video" : "In person"}</small>
        <small>Patient <span className="mono">{shortId(a.person)}</span></small>
      </div>
      <Pill kind="ok">Confirmed</Pill>
    </div>
  );
}
