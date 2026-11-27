import { useState } from "react";
import { changePassword, clearDisplayName, getBookingSystem, getBusinessHours, getDisplayName, getEmailStatus, getSettings, resendVerification, saveDisplayName, setBookingSystem, setBusinessHours, setEmail, setResponseTarget, setWaitlistOfferHours, setWeeklyReport, type Staff, securityCall } from "../api";
import { SecurityCards } from "@shared/Security";
import { ChangePassword } from "../ChangePassword";
import { EmailCard } from "../EmailCard";
import { NameCard } from "../NameCard";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

/** The clinic's own response target. Only its administrator can change it. */
function Target({ hours, standard, canSet, onSaved }: { hours: number; standard: number; canSet: boolean; onSaved: () => void }) {
  const [text, setText] = useState(String(hours));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  async function save(value: number | null) {
    setBusy(true); setError(""); setNote("");
    try { await setResponseTarget(value); setNote(value === null ? "Back to the standard target." : "Saved."); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  const n = Number(text);
  return (
    <Card title="Response target" sub="How long you aim to take to answer a booking, a message or a referral. Anything waiting longer is flagged as late.">
      <p>Your target is <b>{hours} hours</b>{hours === standard ? " (the standard)" : ` (the standard is ${standard})`}.</p>
      {canSet ? (
        <div className="form-row">
          <label>Hours (1 to 168)
            <input type="number" min={1} max={168} value={text} onChange={(e) => setText(e.target.value)} aria-label="Response target in hours" style={{ maxWidth: 110 }} />
          </label>{" "}
          <button className="btn" disabled={busy || !Number.isInteger(n) || n < 1 || n > 168} onClick={() => void save(n)}>Save</button>{" "}
          <button className="btn ghost" disabled={busy} onClick={() => void save(null)}>Use the standard</button>
        </div>
      ) : <small>Only your clinic administrator can change this.</small>}
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
    </Card>
  );
}

/** How long a person has to use a time they were offered from the waiting list. */
function OfferWindow({ hours, standard, canSet, onSaved }: { hours: number; standard: number; canSet: boolean; onSaved: () => void }) {
  const [text, setText] = useState(String(hours));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  async function save(value: number | null) {
    setBusy(true); setError(""); setNote("");
    try { await setWaitlistOfferHours(value); setNote(value === null ? "Back to the standard window." : "Saved."); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  const n = Number(text);
  return (
    <Card title="Waiting-list offers" sub="How long someone has to use a time they were offered before it goes to the next person.">
      <p>Your window is <b>{hours} hours</b>{hours === standard ? " (the standard)" : ` (the standard is ${standard})`}.</p>
      {canSet ? (
        <div className="form-row">
          <label>Hours (1 to 168)
            <input type="number" min={1} max={168} value={text} onChange={(e) => setText(e.target.value)} aria-label="Offer window in hours" style={{ maxWidth: 110 }} />
          </label>{" "}
          <button className="btn" disabled={busy || !Number.isInteger(n) || n < 1 || n > 168} onClick={() => void save(n)}>Save</button>{" "}
          <button className="btn ghost" disabled={busy} onClick={() => void save(null)}>Use the standard</button>
        </div>
      ) : <small>Only your clinic administrator can change this.</small>}
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
    </Card>
  );
}

/** The weekly summary email (counts only) to the clinic's administrators. Off until switched on. */
function WeeklyReport({ on, canSet, onSaved }: { on: boolean; canSet: boolean; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(value: boolean) {
    setBusy(true); setError("");
    try { await setWeeklyReport(value); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Weekly summary email" sub="Once a week the clinic's administrators get the main numbers for the last seven days: bookings, missed visits, how full the clinic was and the waiting list. Counts only, never a patient's name.">
      <p>{on ? <Pill kind="ok">On</Pill> : <Pill kind="info">Off</Pill>}</p>
      {canSet
        ? <button className="btn" disabled={busy} onClick={() => void save(!on)}>{on ? "Turn it off" : "Turn it on"}</button>
        : <small>Only your clinic administrator can change this.</small>}
      {error && <div className="error" role="alert">{error}</div>}
    </Card>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Working days and opening times. With these set, response deadlines count only open time. */
function OpeningHours() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getBusinessHours, 60_000, version);
  const [days, setDays] = useState<number[] | null>(null);
  const [opens, setOpens] = useState<string | null>(null);
  const [closes, setCloses] = useState<string | null>(null);
  const [holidays, setHolidays] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  async function save(clear: boolean, d: number[], o: string, c: string, h: string) {
    setBusy(true); setError(""); setNote("");
    try {
      await setBusinessHours(clear ? null : {
        days: d, opens: o, closes: c, holidays: h.split(/[\s,]+/).filter(Boolean),
      });
      setNote(clear ? "Cleared: deadlines count every hour." : "Saved. It applies to journeys that start from now on.");
      setDays(null); setOpens(null); setCloses(null); setHolidays(null); setVersion((v) => v + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Gate result={result}>
      {(b) => {
        const cur = b.business_hours;
        const d = days ?? cur?.days ?? [0, 1, 2, 3, 4];
        const o = opens ?? cur?.opens ?? "09:00";
        const c = closes ?? cur?.closes ?? "17:00";
        const h = holidays ?? (cur?.holidays ?? []).join(", ");
        return (
          <Card title="Opening hours" sub={`Working days and opening times on your clinic's clock (${b.zone}). When set, response deadlines count only the hours you are open.`}>
            <p>{cur ? <>Today: <b>{cur.days.map((n) => WEEKDAYS[n]).join(", ")}</b>, {cur.opens} to {cur.closes}{cur.holidays.length ? `, ${cur.holidays.length} holiday(s)` : ""}.</>
              : <>Not set: deadlines count every hour of the day.</>}</p>
            {b.can_set ? (
              <div className="profile-form">
                <div role="group" aria-label="Working days" className="row" style={{ gap: 18 }}>
                  {WEEKDAYS.map((name, n) => (
                    <label key={name}>
                      <input type="checkbox" checked={d.includes(n)}
                        onChange={() => setDays(d.includes(n) ? d.filter((x) => x !== n) : [...d, n].sort())} /> {name}
                    </label>
                  ))}
                </div>
                <div className="row" style={{ gap: 18 }}>
                  <label>Opens <input type="time" value={o} onChange={(e) => setOpens(e.target.value)} aria-label="Opening time" /></label>
                  <label>Closes <input type="time" value={c} onChange={(e) => setCloses(e.target.value)} aria-label="Closing time" /></label>
                </div>
                <label>Holidays (dates like 2026-12-25, separated by commas)
                  <input value={h} onChange={(e) => setHolidays(e.target.value)} aria-label="Holidays" />
                </label>
                <div className="row">
                  <button className="btn" disabled={busy || d.length === 0 || o >= c} onClick={() => void save(false, d, o, c, h)}>Save</button>
                  <button className="btn ghost" disabled={busy || !cur} onClick={() => void save(true, d, o, c, h)}>Count every hour</button>
                </div>
              </div>
            ) : <small>Only your clinic administrator can change this.</small>}
            {error && <div className="error" role="alert">{error}</div>}
            {note && <div className="success" role="status">{note}</div>}
          </Card>
        );
      }}
    </Gate>
  );
}

/** Who confirms this clinic's bookings: its own staff by hand, or a partner's booking system. */
function BookingSystemCard() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getBookingSystem, 60_000, version);
  const [choice, setChoice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  async function save(value: string) {
    setBusy(true); setError(""); setNote("");
    try { await setBookingSystem(value); setNote("Saved. It applies to bookings asked about from now on."); setChoice(null); setVersion((v) => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Gate result={result}>
      {(b) => {
        const shown = choice ?? b.system;
        const current = b.options.find((o) => o.key === b.system)?.label ?? b.system;
        return (
          <Card title="Who confirms bookings" sub="By default your staff confirm each booking. A clinic can instead ask its own booking system: the booking is then confirmed only when that system says so itself.">
            <p>Today: <b>{current}</b>.</p>
            {b.can_set ? (
              <div className="form-row">
                <label>Confirmed by
                  <select value={shown} onChange={(e) => setChoice(e.target.value)} aria-label="Who confirms bookings">
                    {b.options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                  </select>
                </label>{" "}
                <button className="btn" disabled={busy || shown === b.system} onClick={() => void save(shown)}>Save</button>
              </div>
            ) : <small>Only your clinic administrator can change this.</small>}
            {error && <div className="error" role="alert">{error}</div>}
            {note && <div className="success" role="status">{note}</div>}
          </Card>
        );
      }}
    </Gate>
  );
}

export function Settings({ staff }: { staff: Staff | null }) {
  const [version, setVersion] = useState(0);
  const result = useLoad(getSettings, 60_000, version);
  if (!staff) return <NoStaff />;
  return (
    <Gate result={result}>
      {(s) => (
        <>
          <div className="hero"><div>
            <h1>Settings</h1>
            <p>The rules {staff.organizationName} runs by today. The response target is your own to set (your administrator can change it); the reminder and waiting-list times are set for the whole service.</p>
          </div></div>
          <div className="grid">
            <Target hours={s.response_target_hours} standard={s.response_target_default ?? s.response_target_hours}
              canSet={s.can_set_target ?? false} onSaved={() => setVersion((v) => v + 1)} />
            <OfferWindow hours={s.waitlist_offer_hours} standard={s.waitlist_offer_default ?? s.waitlist_offer_hours}
              canSet={s.can_set_target ?? false} onSaved={() => setVersion((v) => v + 1)} />
            <WeeklyReport on={s.weekly_report ?? false} canSet={s.can_set_target ?? false} onSaved={() => setVersion((v) => v + 1)} />
            <OpeningHours />
            <BookingSystemCard />
            <Card title="Timing rules">
              <Table headers={["Rule", "Value", "What it does"]} empty=""
                rows={[
                  ["Response target", `${s.response_target_hours} hours`, "A booking or message waiting longer is flagged late on the Dashboard and Appointments, and in the daily email."],
                  ["Appointment reminder", `${s.reminder_lead_hours} hours before`, "Patients are reminded this long before a confirmed appointment."],
                  ["Waiting-list offer", `${s.waitlist_offer_hours} hours`, "An offered time nobody used goes back to the next person after this."],
                ]} />
            </Card>
            <Card title="Daily late-work email">
              {s.late_email_recipients === 0
                ? <p><Pill kind="warn">nobody would receive it</Pill> No clinic operations staff member has a verified email address, so late work is only visible on screen. An address is verified when its owner clicks the link in the email we send them (for example the invitation).</p>
                : <p>{s.late_email_recipients} clinic operations {s.late_email_recipients === 1 ? "person" : "people"} would receive it, once a day, when something is late.</p>}
            </Card>
            <NameCard load={getDisplayName} onSave={saveDisplayName} onClear={clearDisplayName} />
            <EmailCard load={getEmailStatus} onSave={setEmail} onResend={resendVerification} />
            <ChangePassword onChange={changePassword} />
            <SecurityCards call={securityCall} />
          </div>
        </>
      )}
    </Gate>
  );
}
