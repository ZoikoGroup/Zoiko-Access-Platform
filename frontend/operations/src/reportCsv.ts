import { reasonText } from "@shared/cancelReasons";
import type { Reports, Timing } from "./types";

/** One cell, safe for a spreadsheet: quoted when needed, and a leading = + - @ is neutralised so a
 *  service name can never run as a formula when the file is opened. */
export function cell(value: string | number | null): string {
  let text = value === null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text) && typeof value === "string") text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The whole Reports page as plain rows: section, measure, value. Counts only, like the page.
 *  A small number is written "fewer than K"; a part whose measure is not approved, "not shown"
 *  (DEV-389). */
export function reportRows(r: Reports): (string | number | null)[][] {
  const k = r.small_cell_k ?? 5;
  const v = (n: number | null | undefined) => (n === null || n === undefined ? `fewer than ${k}` : n);
  const hidden = (section: string): (string | number | null)[][] => [[section, "not shown", "the measure is not approved yet"]];
  const t = (name: string, x: Timing | null | undefined): (string | number | null)[][] => !x ? hidden("Response times") : [
    ["Response times", `${name}: how many`, v(x.count)],
    ["Response times", `${name}: middle value (hours)`, x.count === null ? "not shown" : x.median],
    ["Response times", `${name}: average (hours)`, x.count === null ? "not shown" : x.average],
  ];
  const b = r.bookings;
  const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  return [
    ["Section", "Measure", "Value"],
    ["Period", "From", r.from],
    ["Period", "To", r.to],
    ["Period", "Days run", `midnight to midnight ${r.zone}${r.zone_source === "places" ? " (the clinic's own clock)" : " (no single clinic clock)"}`],
    ["Period", "Report reference", r.evidence ?? ""],
    ["Bookings", "Made", v(b.total)],
    ...(b.status ? [
      ["Bookings", "Waiting for confirmation now", v(b.status.requested)],
      ["Bookings", "Confirmed now", v(b.status.confirmed)],
      ["Bookings", "Cancelled now", v(b.status.cancelled)]] : hidden("Bookings")),
    ...(b.attendance ? [
      ["Attendance", "Attended", v(b.attendance.attended)],
      ["Attendance", "Did not attend", v(b.attendance.no_show)],
      ["Attendance", "Not recorded yet", v(b.attendance.not_recorded)]] : hidden("Attendance")),
    ...(b.cancel_reasons ? Object.entries(b.cancel_reasons).map(([code, n]) => [
      "Cancellation reasons", code === "none" ? "No reason given" : reasonText(code), v(n)])
      : hidden("Cancellation reasons")),
    ...(b.busiest_services ? b.busiest_services.map((s) => ["Busiest services", s.name, v(s.count)])
      : hidden("Busiest services")),
    ...(b.by_weekday ? weekdays.map((day, i) => ["Appointments by weekday", day, v(b.by_weekday?.[i] ?? 0)])
      : hidden("Appointments by weekday")),
    ...(b.by_doctor ? b.by_doctor.flatMap((d) => [
      ["Visits by doctor", `${d.name}: attended`, v(d.attended)],
      ["Visits by doctor", `${d.name}: did not attend`, v(d.no_show)],
      ["Visits by doctor", `${d.name}: not recorded yet`, v(d.not_recorded)],
    ]) : hidden("Visits by doctor")),
    ...(r.referrals ? [
      ["Referrals", "Received", v(r.referrals.received)],
      ["Referrals", "Still to decide", v(r.referrals.issued)],
      ["Referrals", "Accepted", v(r.referrals.accepted)],
      ["Referrals", "Waiting for more information", v(r.referrals.needs_info)],
      ["Referrals", "Declined", v(r.referrals.declined)]] : hidden("Referrals")),
    ...(r.waiting_list ? [
      ["Waiting lists", "Joined", v(r.waiting_list.joined)],
      ["Waiting lists", "Booked since", v(r.waiting_list.booked)],
      ["Waiting lists", "Left the list", v(r.waiting_list.left)],
      ["Waiting lists", "Waiting now", v(r.waiting_list.waiting_now)]] : hidden("Waiting lists")),
    ...(r.messages ? [
      ["Messages", "Conversations started", v(r.messages.started)],
      ["Messages", "Closed", v(r.messages.closed)],
      ["Messages", "Waiting for the clinic", v(r.messages.waiting_for_clinic)]] : hidden("Messages")),
    ...t("Time to confirm", b.hours_to_confirm),
    ...t("Time to first reply", r.messages?.hours_to_first_reply),
    ...(b.per_day ? b.per_day.map((d) => ["Bookings per day", d.date, v(d.count)]) : hidden("Bookings per day")),
  ];
}

export const reportCsv = (r: Reports): string =>
  reportRows(r).map((row) => row.map(cell).join(",")).join("\r\n") + "\r\n";

/** Save text as a file from memory (no server call: the numbers are already on the page). */
export function saveText(name: string, text: string, type = "text/csv;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob(["﻿" + text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
