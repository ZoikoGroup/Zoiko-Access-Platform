import { useState } from "react";
import { getReports, type Staff } from "../api";
import type { Reports as ReportsData, Timing } from "../types";
import { Gate, useLoad } from "../useLoad";
import { Card, Donut, Pill, StatCard, Table } from "../ui";
import { NoStaff } from "./Appointments";
import { reasonText } from "@shared/cancelReasons";
import { reportCsv, saveText } from "../reportCsv";

/** Hours as people say them: minutes under an hour, hours under two days, then days. */
function hoursText(h: number | null): string {
  if (h === null) return "-";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${h} h`;
  return `${Math.round(h / 24)} days`;
}

const timingNote = (t: Timing, what: string) =>
  t.count === 0 ? `no ${what} yet` : `average ${hoursText(t.average)}, from ${t.count} ${what}`;

/** Bookings made per day, as plain SVG bars (no chart library). */
function Bars({ days }: { days: { date: string; count: number }[] }) {
  const top = Math.max(1, ...days.map((d) => d.count));
  const width = 100 / days.length;
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" width="100%" height="140" role="img"
      aria-label={`Bookings made per day, busiest day ${top}`}>
      {days.map((d, i) => {
        const h = (d.count / top) * 36;
        return (
          <rect key={d.date} x={i * width + width * 0.15} y={38 - h} width={width * 0.7} height={Math.max(h, d.count ? 0.8 : 0.3)}
            fill="var(--brand)" opacity={d.count ? 1 : 0.25}>
            <title>{`${d.date}: ${d.count}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

function View({ r, days, onDays }: { r: ReportsData; days: number; onDays: (n: number) => void }) {
  const att = r.bookings.attendance;
  const held = att.attended + att.no_show;
  const rate = held > 0 ? `${Math.round((att.attended / held) * 100)}%` : "-";
  return (
    <>
      <div className="hero">
        <div>
          <h1>Reports</h1>
          <p>Numbers for {r.from} to {r.to}. Counts only: no patient or message is identified here. Days run from midnight to midnight UTC, except the day-of-week table, which uses each clinic's own clock.</p>
        </div>
        <span className="spacer" />
        <button className="btn ghost" onClick={() => saveText(`zoiko-report-${r.from}-to-${r.to}.csv`, reportCsv(r))}>Download CSV</button>
        <label className="chip">Period
          <select value={days} onChange={(e) => onDays(Number(e.target.value))} aria-label="Reporting period">
            <option value={7}>last 7 days</option>
            <option value={30}>last 30 days</option>
            <option value={90}>last 90 days</option>
          </select>
        </label>
      </div>

      <div className="grid stats">
        <StatCard icon="appointments" label="Bookings made" value={r.bookings.total} note="in this period" />
        <StatCard icon="check" label="Attendance" value={rate} note={held ? `${att.attended} of ${held} recorded visits` : "no visits recorded yet"} />
        <StatCard icon="referrals" label="Referrals received" value={r.referrals.received} note={`${r.referrals.accepted} accepted, ${r.referrals.declined} declined`} />
        <StatCard icon="messages" label="Conversations" value={r.messages.started} note={`${r.messages.waiting_for_clinic} waiting for the clinic`} />
        <StatCard icon="bolt" label="Time to confirm" value={hoursText(r.bookings.hours_to_confirm.median)}
          note={`middle value; ${timingNote(r.bookings.hours_to_confirm, "confirmations")}`} />
        <StatCard icon="bolt" label="Time to first reply" value={hoursText(r.messages.hours_to_first_reply.median)}
          note={`middle value; ${timingNote(r.messages.hours_to_first_reply, "replies")}`} />
      </div>

      <div className="grid cols-2">
        <Card title="Bookings made each day" sub="Each bar is one day, midnight to midnight UTC (hover for the date)"><Bars days={r.bookings.per_day} /></Card>
        <Card title="Where bookings stand now">
          <Donut centerLabel="bookings" slices={[
            { label: "Confirmed", value: r.bookings.status.confirmed, color: "#138a4d" },
            { label: "Waiting", value: r.bookings.status.requested, color: "#d99a00" },
            { label: "Cancelled", value: r.bookings.status.cancelled, color: "#b3261e" },
          ]} />
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Past confirmed appointments" sub="Visits that already happened in this period">
          <Donut centerLabel="visits" slices={[
            { label: "Attended", value: att.attended, color: "#138a4d" },
            { label: "Did not attend", value: att.no_show, color: "#b3261e" },
            { label: "Not recorded yet", value: att.not_recorded, color: "#8a94a6" },
          ]} />
        </Card>
        <Card title="Why bookings were cancelled" sub="Cancellations of bookings made in this period, with the reason the patient or the clinic gave">
          <Table headers={["Reason", "Bookings"]} empty="No cancellations in this period."
            rows={Object.entries(r.bookings.cancel_reasons).sort((a, b) => b[1] - a[1])
              .map(([code, n]) => [code === "none" ? "No reason given" : reasonText(code), n])} />
        </Card>
        <Card title="Missed visits by doctor" sub="Of the visits already recorded as attended or missed">
          <Table headers={["Doctor", "Attended", "Did not attend", "Not recorded", "Missed"]}
            empty="No past confirmed appointments in this period."
            rows={r.bookings.by_doctor.map((d) => [
              d.name, d.attended, d.no_show, d.not_recorded,
              d.no_show_rate === null ? "-" : `${Math.round(d.no_show_rate * 100)}%`])} />
        </Card>
        <Card title="Busiest days of the week" sub="Appointments in this period, by the clinic's own day">
          <Table headers={["Day", "Appointments"]} empty=""
            rows={WEEKDAYS.map((day, i) => [day, r.bookings.by_weekday[i] ?? 0])} />
        </Card>
        {r.forecast && (
          <Card title="The next 7 days" sub="Appointments booked so far, the free times still open, and what that weekday usually has">
            {!r.forecast.enough_history ? (
              <div className="empty">Not enough history yet to say what a usual day looks like (we need about two weeks of appointments). Booked appointments are shown on their own.</div>
            ) : null}
            <Table headers={["Day", "Booked so far", "Free times left", "Usual for this weekday", ""]} empty=""
              rows={r.forecast.days.map((d) => [
                `${WEEKDAYS[d.weekday]} ${d.date}`,
                d.booked,
                d.open === 0 ? <Pill key="o" kind="warn">none left</Pill> : d.open,
                d.usual === null ? "-" : d.usual,
                d.usual !== null && d.booked > d.usual * 1.25 && d.booked >= 3 ? <Pill key="b" kind="warn">busier than usual</Pill> : "",
              ])} />
            <small>A plain average of the last weeks, by the clinic's own day. It is not a trained prediction and ignores holidays, so use it as a guide only.</small>
          </Card>
        )}
        <Card title="Busiest services">
          <Table headers={["Service", "Bookings"]} empty="No bookings in this period."
            rows={r.bookings.busiest_services.map((s) => [s.name, s.count])} />
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Waiting lists">
          <Table headers={["", "People"]} empty=""
            rows={[["Joined in this period", r.waiting_list.joined], ["Of those, booked since", r.waiting_list.booked],
              ["Of those, left the list", r.waiting_list.left], ["Waiting right now", r.waiting_list.waiting_now]]} />
        </Card>
        <Card title="Referrals and messages">
          <Table headers={["", "Number"]} empty=""
            rows={[["Referrals still to decide", r.referrals.issued], ["Referrals waiting for more information", r.referrals.needs_info],
              ["Conversations started", r.messages.started], ["Of those, closed", r.messages.closed]]} />
        </Card>
      </div>
    </>
  );
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function Reports({ staff }: { staff: Staff | null }) {
  const [days, setDays] = useState(30);
  const result = useLoad(() => getReports(days), 60_000, days);
  if (!staff) return <NoStaff />;
  return <Gate result={result}>{(r) => <View r={r} days={days} onDays={setDays} />}</Gate>;
}
