import { useState } from "react";
import { changePassword, clearDisplayName, getDisplayName, getEmailStatus, getSettings, resendVerification, saveDisplayName, setEmail, setResponseTarget, setWaitlistOfferHours, getBusinessHours, setBusinessHours, getTwoStepRule, setTwoStepRule, twoStepApi, getSupportAccess, supportAccessStep, type SupportAccessRow, type Staff } from "../api";
import { TwoStepCard } from "@shared/TwoStep";
import { ChangePassword } from "../ChangePassword";
import { EmailCard } from "../EmailCard";
import { NameCard } from "../NameCard";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

/** The customer's rule (DEV-380): every staff login must use two-step sign-in. Only the
 *  administrator changes it, and must have it on themselves first. */
function TwoStepRule() {
  const [version, setVersion] = useState(0);
  const rule = useLoad(getTwoStepRule, 60_000, version);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(required: boolean) {
    setBusy(true); setError("");
    try { await setTwoStepRule(required); setVersion((v) => v + 1); }
    catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "CONFLICT_VERSION" ? "Turn on two-step sign-in for your own login first (below)." : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }
  const r = rule.data;
  return (
    <Card title="Two-step sign-in for all staff"
      sub="When required, every staff login here must enter a code from an authenticator app after its password. Patients are never asked.">
      {!r ? <p>Loading…</p> : <>
        <p><Pill kind={r.required ? "ok" : "info"}>{r.required ? "Required" : "Not required"}</Pill>{" "}
          {r.staff_without_two_step} staff login{r.staff_without_two_step === 1 ? " has" : "s have"} not set it up
          {r.required ? " (they set it up the next time they sign in)" : ""}.</p>
        {r.can_set && <div style={{ display: "flex", gap: 8 }}>
          {r.required
            ? <button className="btn ghost" type="button" disabled={busy} onClick={() => void save(false)}>Stop requiring it</button>
            : <button className="btn" type="button" disabled={busy || !r.mine_on} onClick={() => void save(true)}>Require it for all staff</button>}
        </div>}
        {r.can_set && !r.required && !r.mine_on && <p className="muted">Turn it on for your own login first.</p>}
        {error && <div className="error" role="alert">{error}</div>}
      </>}
    </Card>
  );
}

const SUPPORT_STATE: Record<SupportAccessRow["state"], { text: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  requested: { text: "Waiting for your answer", kind: "warn" },
  lapsed: { text: "Not answered in time", kind: "info" },
  active: { text: "Active now", kind: "ok" },
  expired: { text: "Time is up", kind: "info" },
  refused: { text: "Refused", kind: "bad" },
  ended: { text: "Ended", kind: "info" },
};

/** Zoiko support asking for short access to this organization (DEV-381). Only the administrator
 *  approves, refuses or ends it; support never sees patients, and every look is listed here. */
function SupportAccessCard() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getSupportAccess, 30_000, version);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function step(id: string, what: "approve" | "refuse" | "end") {
    setBusy(id); setError("");
    try { await supportAccessStep(id, what); setVersion((v) => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }
  const d = result.data;
  const labels = Object.fromEntries((d?.scopes ?? []).map((s) => [s.code, s.label]));
  return (
    <Card title="Support access"
      sub="Zoiko support has no access to your organization unless you approve it, for a few minutes at a time. They see setup and status only, never patients, and every look is recorded in your audit log.">
      {!d ? <p>Loading…</p> : d.access.length === 0 ? <p>Zoiko support has never asked for access.</p> : (
        <div style={{ display: "grid", gap: 10 }}>
          {d.access.map((r) => (
            <div key={r.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <div className="info">
                <b>{r.requester} · ticket {r.ticket}</b>{" "}
                <Pill kind={SUPPORT_STATE[r.state].kind}>{SUPPORT_STATE[r.state].text}</Pill>
                <small>Asked {r.asked_at.slice(0, 16).replace("T", " ")} for {r.minutes} minutes
                  {r.ends_at && r.state === "active" ? ` · ends ${r.ends_at.slice(11, 16)}` : ""}
                  {r.decided_by ? ` · decided by ${r.decided_by}` : ""} · {r.uses} look{r.uses === 1 ? "" : "s"}</small>
                <small>Why: {r.reason}</small>
                <small>Would see: {r.scopes.map((s) => labels[s] ?? s).join("; ")}</small>
              </div>
              {d.can_decide && (r.state === "requested" || r.state === "active") && (
                <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                  {r.state === "requested" && <>
                    <button className="btn" type="button" disabled={busy === r.id} onClick={() => void step(r.id, "approve")}>Approve</button>
                    <button className="btn ghost" type="button" disabled={busy === r.id} onClick={() => void step(r.id, "refuse")}>Refuse</button>
                  </>}
                  {r.state === "active" && <button className="btn ghost" type="button" disabled={busy === r.id} onClick={() => void step(r.id, "end")}>End now</button>}
                </div>
              )}
            </div>
          ))}
          {!d.can_decide && <p className="muted">Only your organization's administrator can approve or end support access.</p>}
        </div>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </Card>
  );
}

/** One of the clinic's own time rules (the response target, the waiting-list offer window). Only its
 * administrator can change it. */
function Target({ hours, standard, canSet, onSaved, title = "Response target",
  sub = "How long you aim to take to answer a booking, a message or a referral. Anything waiting longer is flagged as late.",
  noun = "target", saveHours = setResponseTarget }: {
  hours: number; standard: number; canSet: boolean; onSaved: () => void;
  title?: string; sub?: string; noun?: string; saveHours?: (hours: number | null) => Promise<unknown>;
}) {
  const [text, setText] = useState(String(hours));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  async function save(value: number | null) {
    setBusy(true); setError(""); setNote("");
    try { await saveHours(value); setNote(value === null ? `Back to the standard ${noun}.` : "Saved."); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  const n = Number(text);
  return (
    <Card title={title} sub={sub}>
      <p>Your {noun} is <b>{hours} hours</b>{hours === standard ? " (the standard)" : ` (the standard is ${standard})`}.</p>
      {canSet ? (
        <div className="form-row">
          <label>Hours (1 to 168)
            <input type="number" min={1} max={168} value={text} onChange={(e) => setText(e.target.value)} aria-label={`${title} in hours`} style={{ maxWidth: 110 }} />
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

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** The clinic's working days and opening times (DEV-367, from the maruthi branch). */
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
      setNote(clear ? "Cleared: deadlines count every hour." : "Saved. Late flags, the work queue and late counts use it from now on.");
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
          <Card title="Opening hours" sub={`Working days and opening times on your clinic's clock (${b.zone}). When set, your response target counts only the hours you are open: late flags, the work queue, the dashboard and the daily late email all use it. Legal deadlines (data requests from a country pack) never stretch.`}>
            {b.zone_source !== "places" && <p><Pill kind="warn">no single clock</Pill> Your places use different time zones, or you have none yet, so the hours are read in UTC.</p>}
            <p>{cur ? <>Today: <b>{cur.days.map((n) => WEEKDAYS[n]).join(", ")}</b>, {cur.opens} to {cur.closes}{cur.holidays.length ? `, ${cur.holidays.length} holiday(s)` : ""}.</>
              : <>Not set: deadlines count every hour of the day.</>}</p>
            {b.can_set ? (
              <div className="form-row">
                <div role="group" aria-label="Working days">
                  {WEEKDAYS.map((name, n) => (
                    <label key={name} style={{ marginRight: 8 }}>
                      <input type="checkbox" checked={d.includes(n)}
                        onChange={() => setDays(d.includes(n) ? d.filter((x) => x !== n) : [...d, n].sort())} /> {name}
                    </label>
                  ))}
                </div>
                <label>Opens <input type="time" value={o} onChange={(e) => setOpens(e.target.value)} aria-label="Opening time" /></label>{" "}
                <label>Closes <input type="time" value={c} onChange={(e) => setCloses(e.target.value)} aria-label="Closing time" /></label>
                <label style={{ display: "block" }}>Holidays (dates like 2026-12-25, separated by commas)
                  <input value={h} onChange={(e) => setHolidays(e.target.value)} aria-label="Holidays" style={{ width: "100%" }} />
                </label>
                <button className="btn" disabled={busy || d.length === 0 || o >= c} onClick={() => void save(false, d, o, c, h)}>Save</button>{" "}
                <button className="btn ghost" disabled={busy || !cur} onClick={() => void save(true, d, o, c, h)}>Count every hour</button>
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
            <p>The rules {staff.organizationName} runs by today. The response target and the waiting-list offer window are your own to set (your administrator can change them); the reminder time is set for the whole service.</p>
          </div></div>
          <div className="grid">
            <Target hours={s.response_target_hours} standard={s.response_target_default ?? s.response_target_hours}
              canSet={s.can_set_target ?? false} onSaved={() => setVersion((v) => v + 1)} />
            <Target key={`offer-${s.waitlist_offer_hours}`} hours={s.waitlist_offer_hours}
              standard={s.waitlist_offer_default ?? s.waitlist_offer_hours}
              canSet={s.can_set_target ?? false} onSaved={() => setVersion((v) => v + 1)}
              title="Waiting-list offer window" noun="offer window" saveHours={setWaitlistOfferHours}
              sub="How long a patient has to book a time offered from the waiting list before it goes to the next person." />
            <OpeningHours />
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
            <TwoStepCard api={twoStepApi} />
            <TwoStepRule />
            <SupportAccessCard />
          </div>
        </>
      )}
    </Gate>
  );
}
