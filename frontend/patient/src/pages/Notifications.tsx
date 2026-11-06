import { useEffect, useState } from "react";
import { getEmailNotices, markNotificationRead, setEmailNotices, type EmailNotices, type Notice } from "../api";
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

  return (
    <Card title="Email me about changes"
      sub="A short email when an appointment is confirmed, cancelled, moved or coming up, or when your clinic writes to you. It never says which clinic, service or time: you sign in to see the details.">
      {state === null ? <div className="empty">{error || "Loading…"}</div> : (
        <>
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" checked={state.enabled} disabled={busy} onChange={(e) => void change(e.target.checked)} />
            Send me these emails
          </label>
          {state.enabled && !state.can_email && (
            <p><Pill kind="warn">no confirmed address</Pill> Nothing is sent until you add an email address and confirm it in <button className="link" onClick={goToProfile}>My Profile</button>.</p>
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
