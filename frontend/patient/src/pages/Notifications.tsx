import { useState } from "react";
import { markNotificationRead, type Notice } from "../api";
import { Card, Pill, type PillKind } from "../ui";

const KIND: Record<string, { label: string; kind: PillKind }> = {
  booking_requested: { label: "Requested", kind: "warn" },
  booking_confirmed: { label: "Confirmed", kind: "ok" },
  booking_cancelled: { label: "Cancelled", kind: "bad" },
  referral_issued: { label: "Referral", kind: "info" },
  referral_accepted: { label: "Referral accepted", kind: "ok" },
  referral_needs_info: { label: "Referral", kind: "warn" },
  referral_declined: { label: "Referral declined", kind: "bad" },
};

function when(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function Notifications({ notices, unread, onChanged }: {
  notices: Notice[]; unread: number; onChanged: () => Promise<void>;
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
        <Card title={`${notices.length} notification${notices.length === 1 ? "" : "s"}`}>
          {notices.length === 0 ? <div className="empty">Nothing yet. Updates appear here when you book, or when a clinic confirms or cancels.</div> : notices.map((n) => (
            <div key={n.id} className={`appt note ${n.read ? "" : "unread"}`}>
              <div className="info">
                <b>{!n.read && <span className="unread-dot" aria-label="unread" />}{n.title}</b>
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
