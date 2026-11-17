import { useEffect, useState } from "react";
import { getEmailNotices, markNotificationRead, setEmailNotices, confirmPhoneCode, sendPhoneCode, setQuietHours, setTexts, type EmailNotices, type Notice } from "../api";
import { Card, Pill, type PillKind } from "../ui";

const KIND: Record<string, { label: string; kind: PillKind }> = {
  booking_requested: { label: "Requested", kind: "warn" },
  booking_confirmed: { label: "Confirmed", kind: "ok" },
  booking_cancelled: { label: "Cancelled", kind: "bad" },
  booking_rescheduled: { label: "Moved", kind: "info" },
  booking_reminder: { label: "Reminder", kind: "warn" },
  waitlist_offer: { label: "Time open", kind: "ok" },
  family_approved: { label: "Family", kind: "ok" },
  message_received: { label: "Message", kind: "info" },
  family_rejected: { label: "Family", kind: "warn" },
  booking_attended: { label: "Attended", kind: "ok" },
  booking_no_show: { label: "Missed", kind: "bad" },
  referral_issued: { label: "Referral", kind: "info" },
  referral_accepted: { label: "Referral accepted", kind: "ok" },
  referral_needs_info: { label: "Referral", kind: "warn" },
  referral_declined: { label: "Referral declined", kind: "bad" },
};

function when(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** The switch for short emails ("something changed, sign in to see"). Off until turned on, and it
 *  needs an email address the patient has confirmed (My Profile). */
function EmailSwitch({ goToProfile }: { goToProfile: () => void }) {
  const [state, setState] = useState<EmailNotices | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getEmailNotices().then(setState).catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, []);

  async function change(enabled: boolean) {
    setBusy(true); setError("");
    try { setState(await setEmailNotices(enabled)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not change it"); }
    finally { setBusy(false); }
  }

  const [times, setTimes] = useState({ start: "22:00", end: "07:00" });
  const [codeSent, setCodeSent] = useState(false);
  const [code, setCode] = useState("");
  useEffect(() => {
    if (state?.quiet) setTimes({ start: state.quiet.start, end: state.quiet.end });
  }, [state?.quiet]);
  async function saveQuiet(clear: boolean) {
    setBusy(true); setError("");
    try {
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      setState(await setQuietHours(clear ? null : { start: times.start, end: times.end, zone }));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not change it"); }
    finally { setBusy(false); }
  }

  return (
    <Card title="Email me about changes"
      sub="A short email when an appointment is confirmed, cancelled, moved or coming up, or when your clinic writes to you. It never says which clinic, service or time: you sign in to see the details.">
      {state === null ? <div className="empty">{error || "Loading…"}</div> : (
        <>
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" checked={state.enabled} disabled={busy} onChange={(e) => void change(e.target.checked)} />
            Send me these emails
          </label>
          {state.enabled && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              <input type="checkbox" checked={state.texts} disabled={busy}
                onChange={async (e) => { const on = e.target.checked; setBusy(true); setError(""); try { setState(await setTexts(on)); } catch (err) { setError(err instanceof Error ? err.message : "Could not change it"); } finally { setBusy(false); } }} />
              Also send me a text message (it only says there is an update; your phone number must start with + and the country code)
            </label>
          )}
          {state.enabled && state.texts && (state.phone_verified ? (
            <p className="sub">Your phone number is verified for texts.</p>
          ) : (
            <div className="form-row" style={{ marginTop: 8 }}>
              <span className="sub">Texts are sent only after you prove the number is yours.</span>
              {codeSent ? (
                <>
                  <input aria-label="The six-digit code we texted you" inputMode="numeric" maxLength={6} value={code}
                    onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, ""))} />
                  <button className="btn" disabled={busy || code.length !== 6}
                    onClick={async () => { setBusy(true); setError(""); try { await confirmPhoneCode(code); setState(await getEmailNotices()); setCodeSent(false); setCode(""); } catch { setError("That code did not work. Check it, or ask for a new one."); } finally { setBusy(false); } }}>
                    Confirm
                  </button>
                </>
              ) : (
                <button className="btn ghost" disabled={busy}
                  onClick={async () => { setBusy(true); setError(""); try { await sendPhoneCode(); setCodeSent(true); } catch { setError("We could not send a code. Check that your number in My Profile starts with + and the country code, or try again in an hour."); } finally { setBusy(false); } }}>
                  Text me a code
                </button>
              )}
            </div>
          ))}
          {state.enabled && !state.can_email && (
            <p><Pill kind="warn">no confirmed address</Pill> Nothing is sent until you add an email address and confirm it in <button className="link" onClick={goToProfile}>My Profile</button>.</p>
          )}
          {state.enabled && (
            <fieldset style={{ border: 0, padding: 0, margin: "12px 0 0" }}>
              <legend><b>Quiet hours</b></legend>
              <p className="sub">Reminder emails wait until these hours end. Changes to an appointment, and messages from your clinic, are never held back.</p>
              <div className="form-row">
                <label>From <input type="time" value={times.start} disabled={busy} onChange={(e) => setTimes({ ...times, start: e.target.value })} /></label>
                <label>Until <input type="time" value={times.end} disabled={busy} onChange={(e) => setTimes({ ...times, end: e.target.value })} /></label>
                <button className="btn" disabled={busy || !times.start || !times.end || times.start === times.end} onClick={() => void saveQuiet(false)}>Save quiet hours</button>
                {state.quiet && <button className="btn ghost" disabled={busy} onClick={() => void saveQuiet(true)}>Clear</button>}
              </div>
              {state.quiet && <p className="sub">Quiet from {state.quiet.start} to {state.quiet.end} ({state.quiet.zone}).</p>}
            </fieldset>
          )}
          {error && <div className="error" role="alert">{error}</div>}
        </>
      )}
    </Card>
  );
}

export function Notifications({ notices, unread, onChanged, goToProfile }: {
  notices: Notice[]; unread: number; onChanged: () => Promise<void>; goToProfile: () => void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function read(ids: string[]) {
    setError("");
    try {
      for (const id of ids) {
        setBusy(id);
        await markNotificationRead(id);
      }
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update");
    } finally { setBusy(""); }
  }

  const unreadIds = notices.filter((n) => !n.read).map((n) => n.id);
  return (
    <>
      <div className="hero">
        <div>
          <h1>Notifications</h1>
          <p>Updates about your appointments, newest first. {unread > 0 ? <b>{unread} unread.</b> : "You are all caught up."}</p>
        </div>
        <span className="spacer" />
        <button className="btn ghost" disabled={unreadIds.length === 0 || busy !== ""} onClick={() => void read(unreadIds)}>
          Mark all as read
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <EmailSwitch goToProfile={goToProfile} />
        <Card title={`${notices.length} notification${notices.length === 1 ? "" : "s"}`}>
          {notices.length === 0 ? <div className="empty">Nothing yet. Updates appear here when you book, or when a clinic confirms or cancels.</div> : notices.map((n) => (
            <div key={n.id} className={`appt note ${n.read ? "" : "unread"}`}>
              <div className="info">
                <b>{!n.read && <span className="unread-dot" role="img" aria-label="unread" />}{n.title}</b>
                <small>{n.body}</small>
                <small>{when(n.created_at)}</small>
              </div>
              <Pill kind={(KIND[n.kind] ?? { kind: "info" as PillKind }).kind}>{(KIND[n.kind] ?? { label: n.kind }).label}</Pill>
              {!n.read && (
                <button className="btn ghost" disabled={busy === n.id} onClick={() => void read([n.id])}>Mark as read</button>
              )}
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
