import { reasonText } from "@shared/cancelReasons";
import type { Reports } from "./types";

/** One cell, safe for a spreadsheet: quoted when needed, and a leading = + - @ is neutralised so a
 *  service name can never run as a formula when the file is opened. */
export function cell(value: string | number | null): string {
  let text = value === null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text) && typeof value === "string") text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The whole Reports page as plain rows: section, measure, value. Counts only, like the page. */
export function reportRows(r: Reports): (string | number | null)[][] {
  const t = (name: string, x: { count: number; median: number | null; average: number | null }) => [
    ["Response times", `${name}: how many`, x.count],
    ["Response times", `${name}: middle value (hours)`, x.median],
    ["Response times", `${name}: average (hours)`, x.average],
  ];
  return [
    ["Section", "Measure", "Value"],
    ["Period", "From", r.from],
    ["Period", "To", r.to],
    ["Period", "Days run", "midnight to midnight UTC (the weekday table uses the clinic clock)"],
    ["Bookings", "Made", r.bookings.total],
    ["Bookings", "Waiting for confirmation now", r.bookings.status.requested],
    ["Bookings", "Confirmed now", r.bookings.status.confirmed],
    ["Bookings", "Cancelled now", r.bookings.status.cancelled],
    ["Attendance", "Attended", r.bookings.attendance.attended],
    ["Attendance", "Did not attend", r.bookings.attendance.no_show],
    ["Attendance", "Not recorded yet", r.bookings.attendance.not_recorded],
    ...Object.entries(r.bookings.cancel_reasons).map(([code, n]) => [
      "Cancellation reasons", code === "none" ? "No reason given" : reasonText(code), n]),
    ...r.bookings.busiest_services.map((s) => ["Busiest services", s.name, s.count]),
    ...["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day, i) => [
      "Appointments by weekday", day, r.bookings.by_weekday[i] ?? 0]),
    ...r.bookings.by_doctor.flatMap((d) => [
      ["Visits by doctor", `${d.name}: attended`, d.attended],
      ["Visits by doctor", `${d.name}: did not attend`, d.no_show],
      ["Visits by doctor", `${d.name}: not recorded yet`, d.not_recorded],
    ]),
    ["Referrals", "Received", r.referrals.received],
    ["Referrals", "Still to decide", r.referrals.issued],
    ["Referrals", "Accepted", r.referrals.accepted],
    ["Referrals", "Waiting for more information", r.referrals.needs_info],
    ["Referrals", "Declined", r.referrals.declined],
    ["Waiting lists", "Joined", r.waiting_list.joined],
    ["Waiting lists", "Booked since", r.waiting_list.booked],
    ["Waiting lists", "Left the list", r.waiting_list.left],
    ["Waiting lists", "Waiting now", r.waiting_list.waiting_now],
    ["Messages", "Conversations started", r.messages.started],
    ["Messages", "Closed", r.messages.closed],
    ["Messages", "Waiting for the clinic", r.messages.waiting_for_clinic],
    ...t("Time to confirm", r.bookings.hours_to_confirm),
    ...t("Time to first reply", r.messages.hours_to_first_reply),
    ...r.bookings.per_day.map((d) => ["Bookings per day", d.date, d.count]),
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
