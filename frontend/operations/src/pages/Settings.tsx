import { useState } from "react";
import { changePassword, clearDisplayName, getBookingSystem, getDisplayName, getEmailStatus, getSettings, resendVerification, saveDisplayName, setBookingSystem, setEmail, setResponseTarget, type Staff } from "../api";
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
          </div>
        </>
      )}
    </Gate>
  );
}
