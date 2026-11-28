import { useState } from "react";
import { getReports, type Staff } from "../api";
import type { Count, Reports as ReportsData, Timing } from "../types";
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

/** A count as shown: small numbers are not shown (DEV-389, GHE-10 small-cell protection). */
const show = (n: Count | undefined, k: number) => (n === null || n === undefined ? `fewer than ${k}` : n);
const num = (n: Count | undefined) => (typeof n === "number" ? n : 0);

const timingNote = (t: Timing | null, what: string, k: number) =>
  !t ? "not shown" : t.count === null ? `fewer than ${k} ${what}: not shown`
    : t.count === 0 ? `no ${what} yet` : `average ${hoursText(t.average)}, from ${t.count} ${what}`;

/** Bookings made per day, as plain SVG bars (no chart library). A day with a small count is drawn
 *  as a faint stub: its number is not shown. */
function Bars({ days }: { days: { date: string; count: Count }[] }) {
  const top = Math.max(1, ...days.map((d) => num(d.count)));
  const width = 100 / Math.max(1, days.length);
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" width="100%" height="140" role="img"
      aria-label={`Bookings made per day, busiest day ${top}`}>
      {days.map((d, i) => {
        const h = (num(d.count) / top) * 36;
        return (
          <rect key={d.date} x={i * width + width * 0.15} y={38 - h} width={width * 0.7} height={Math.max(h, d.count ? 0.8 : 0.3)}
            fill="var(--brand)" opacity={d.count ? 1 : 0.25}>
            <title>{`${d.date}: ${d.count === null ? "a few (not shown)" : d.count}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

/** A part of the report whose measure is not approved yet (DEV-389). */
function Withheld({ title }: { title: string }) {
  return <Card title={title}><div className="empty">Not shown: the measure for these numbers is not approved yet.</div></Card>;
}

function View({ r, days, onDays }: { r: ReportsData; days: number; onDays: (n: number) => void }) {
  const k = r.small_cell_k ?? 5;
  const b = r.bookings;
  const att = b.attendance;
  const held = att ? num(att.attended) + num(att.no_show) : 0;
  const exact = att !== null && att.attended !== null && att.no_show !== null;
  const rate = exact && held > 0 ? `${Math.round((num(att?.attended) / held) * 100)}%` : "-";
  const referrals = r.referrals;
  const messages = r.messages;
  return (
    <>
      <div className="hero">
        <div>
          <h1>Reports</h1>
          <p>Numbers for {r.from} to {r.to}. Counts only: no patient or message is identified here. Counts under {k} are not shown, so nobody can be picked out. {clockNote(r.zone, r.zone_source)}</p>
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
        <StatCard icon="appointments" label="Bookings made" value={show(b.total, k)} note="in this period" />
        <StatCard icon="check" label="Attendance" value={rate} note={!att ? "not shown" : !exact ? `fewer than ${k} visits: not shown` : held ? `${att.attended} of ${held} recorded visits` : "no visits recorded yet"} />
        <StatCard icon="referrals" label="Referrals received" value={referrals ? show(referrals.received, k) : "-"} note={referrals ? `${show(referrals.accepted, k)} accepted, ${show(referrals.declined, k)} declined` : "not shown"} />
        <StatCard icon="messages" label="Conversations" value={messages ? show(messages.started, k) : "-"} note={messages ? `${show(messages.waiting_for_clinic, k)} waiting for the clinic` : "not shown"} />
        <StatCard icon="bolt" label="Time to confirm" value={hoursText(b.hours_to_confirm?.median ?? null)}
          note={`middle value; ${timingNote(b.hours_to_confirm, "confirmations", k)}`} />
        <StatCard icon="bolt" label="Time to first reply" value={hoursText(messages?.hours_to_first_reply?.median ?? null)}
          note={`middle value; ${timingNote(messages?.hours_to_first_reply ?? null, "replies", k)}`} />
      </div>

      <div className="grid cols-2">
        {b.per_day ? <Card title="Bookings made each day" sub={`Each bar is one day, midnight to midnight ${r.zone} (hover for the date)`}><Bars days={b.per_day} /></Card> : <Withheld title="Bookings made each day" />}
        {b.status ? (
          <Card title="Where bookings stand now" sub={Object.values(b.status).some((x) => x === null) ? `Some parts are fewer than ${k} and not shown.` : undefined}>
            <Donut centerLabel="bookings" slices={[
              { label: "Confirmed", value: num(b.status.confirmed), color: "#138a4d" },
              { label: "Waiting", value: num(b.status.requested), color: "#d99a00" },
              { label: "Cancelled", value: num(b.status.cancelled), color: "#b3261e" },
            ]} />
          </Card>
        ) : <Withheld title="Where bookings stand now" />}
      </div>

      <div className="grid cols-2">
        {att ? (
          <Card title="Past confirmed appointments" sub={exact ? "Visits that already happened in this period" : `Some parts are fewer than ${k} and not shown.`}>
            <Donut centerLabel="visits" slices={[
              { label: "Attended", value: num(att.attended), color: "#138a4d" },
              { label: "Did not attend", value: num(att.no_show), color: "#b3261e" },
              { label: "Not recorded yet", value: num(att.not_recorded), color: "#8a94a6" },
            ]} />
          </Card>
        ) : <Withheld title="Past confirmed appointments" />}
        {b.cancel_reasons ? (
          <Card title="Why bookings were cancelled" sub="Cancellations of bookings made in this period, with the reason the patient or the clinic gave">
            <Table headers={["Reason", "Bookings"]} empty="No cancellations in this period."
              rows={Object.entries(b.cancel_reasons).sort((x, y) => num(y[1]) - num(x[1]))
                .map(([code, n]) => [code === "none" ? "No reason given" : reasonText(code), show(n, k)])} />
          </Card>
        ) : <Withheld title="Why bookings were cancelled" />}
        {b.by_doctor ? (
          <Card title="Missed visits by doctor" sub={`Of the visits already recorded as attended or missed. A rate needs at least ${k} visits.`}>
            <Table headers={["Doctor", "Attended", "Did not attend", "Not recorded", "Missed"]}
              empty="No past confirmed appointments in this period."
              rows={b.by_doctor.map((d) => [
                d.name, show(d.attended, k), show(d.no_show, k), show(d.not_recorded, k),
                d.no_show_rate === null ? "-" : `${Math.round(d.no_show_rate * 100)}%`])} />
          </Card>
        ) : <Withheld title="Missed visits by doctor" />}
        {b.by_weekday ? (
          <Card title="Busiest days of the week" sub="Appointments in this period, by the clinic's own day">
            <Table headers={["Day", "Appointments"]} empty=""
              rows={WEEKDAYS.map((day, i) => [day, show(b.by_weekday?.[i] ?? 0, k)])} />
          </Card>
        ) : <Withheld title="Busiest days of the week" />}
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
        {r.forecast === null && <Withheld title="The next 7 days" />}
        {b.busiest_services ? (
          <Card title="Busiest services">
            <Table headers={["Service", "Bookings"]} empty="No bookings in this period."
              rows={b.busiest_services.map((x) => [x.name, show(x.count, k)])} />
          </Card>
        ) : <Withheld title="Busiest services" />}
      </div>

      <div className="grid cols-2">
        {r.waiting_list ? (
          <Card title="Waiting lists">
            <Table headers={["", "People"]} empty=""
              rows={[["Joined in this period", show(r.waiting_list.joined, k)], ["Of those, booked since", show(r.waiting_list.booked, k)],
                ["Of those, left the list", show(r.waiting_list.left, k)], ["Waiting right now", show(r.waiting_list.waiting_now, k)]]} />
          </Card>
        ) : <Withheld title="Waiting lists" />}
        <Card title="Referrals and messages">
          <Table headers={["", "Number"]} empty=""
            rows={[["Referrals still to decide", referrals ? show(referrals.issued, k) : "not shown"],
              ["Referrals waiting for more information", referrals ? show(referrals.needs_info, k) : "not shown"],
              ["Conversations started", messages ? show(messages.started, k) : "not shown"],
              ["Of those, closed", messages ? show(messages.closed, k) : "not shown"]]} />
        </Card>
      </div>

      {r.measures && (
        <Card title="How these numbers are counted" sub={`Every number has an approved definition. Counts under ${k} are not shown, and when one number in a group is hidden a second one is too, so it cannot be worked out by subtraction. Report reference ${r.evidence ?? ""}.`}>
          <Table headers={["Measure", "Counts", "Out of", "Leaves out", ""]} empty=""
            rows={r.measures.map((m) => [m.title, m.counts, m.out_of, m.leaves_out,
              m.approved ? <Pill key="a" kind="ok">approved</Pill> : <Pill key="n" kind="warn">not approved</Pill>])} />
        </Card>
      )}
    </>
  );
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function clockNote(zone: string, source: string): string {
  if (source === "places") return `Days run from midnight to midnight on this clinic's own clock (${zone}).`;
  return source === "mixed"
    ? "Days run from midnight to midnight UTC, because this clinic's places use different time zones."
    : "Days run from midnight to midnight UTC, because this clinic has no place with a time zone yet.";
}

export function Reports({ staff }: { staff: Staff | null }) {
  const [days, setDays] = useState(30);
  const result = useLoad(() => getReports(days), 60_000, days);
  if (!staff) return <NoStaff />;
  return <Gate result={result}>{(r) => <View r={r} days={days} onDays={setDays} />}</Gate>;
}
