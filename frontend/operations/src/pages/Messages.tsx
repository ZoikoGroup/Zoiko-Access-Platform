import { useCallback, useEffect, useState } from "react";
import { closeMessageThread, getMessageThread, listMessageThreads, replyMessageThread, type Staff } from "../api";
import type { MessageThread, ThreadView } from "../types";
import { Card, Patient, Pill } from "../ui";
import { NoStaff } from "./Appointments";

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function Conversation({ id, onChanged }: { id: string; onChanged: () => Promise<void> }) {
  const [view, setView] = useState<ThreadView | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setView(await getMessageThread(id)); setError(""); await onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, [id]);                                           // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setView(null); void load(); }, [load]);

  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); setText(""); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  if (!view) return <Card title="Conversation">{error ? <div className="error">{error}</div> : <div className="empty">Loading…</div>}</Card>;
  return (
    <Card title={view.subject} sub={view.status === "closed" ? "Closed" : "Open"}
      action={view.status === "open" ? <button className="btn ghost" disabled={busy} onClick={() => void run(() => closeMessageThread(id))}>Close conversation</button> : undefined}>
      {view.messages.map((m) => (
        <div key={m.id} className="appt" style={{ alignItems: "flex-start" }}>
          <div className="info">
            <b>{m.side === "staff" ? (m.mine ? "You" : m.from || "Clinic staff") : <Patient name={view.patient_name} id={view.patient} />}</b>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.body || "(this message cannot be shown)"}</div>
            <small>{when(m.at)}</small>
            {m.side === "staff" && (
              <small>{m.seen_at ? `Opened by the patient ${when(m.seen_at)}.` : "Sent. The patient has not opened it yet."}</small>
            )}
          </div>
        </div>
      ))}
      {view.status === "open" && (
        <form className="add-form" style={{ marginTop: 12 }} onSubmit={(e) => { e.preventDefault(); void run(() => replyMessageThread(id, text)); }}>
          <textarea value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} aria-label="Your reply"
            placeholder="Write a reply" style={{ minHeight: 80, padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }} />
          {error && <div className="error" role="alert">{error}</div>}
          <div><button className="btn" type="submit" disabled={busy || !text.trim()}>{busy ? "Sending…" : "Send reply"}</button></div>
        </form>
      )}
    </Card>
  );
}

/** Patients writing to this clinic. Staff answer, and close a conversation when it is finished. */
export function Messages({ staff }: { staff: Staff | null }) {
  const [threads, setThreads] = useState<MessageThread[] | null>(null);
  const [open, setOpen] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!staff) return;
    try { setThreads(await listMessageThreads()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the messages"); }
  }, [staff]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  if (!staff) return <NoStaff />;
  const waiting = (threads ?? []).filter((t) => (t.unread_for_me ?? t.unread) > 0).length;
  return (
    <>
      <div className="hero"><div>
        <h1>Messages</h1>
        <p>Patients writing to {staff.organizationName}. Messages are stored encrypted. These are for appointments and everyday questions, not clinical advice. {waiting > 0 ? `${waiting} unread.` : ""}</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {/* Said aloud by a screen reader whenever the number changes (GHE-16 TC34, DEV-157). */}
      <div className="sr-only" role="status" aria-live="polite" data-testid="inbox-status">
        {threads === null ? "" : waiting === 0 ? "No conversations waiting." : `${waiting} conversation${waiting === 1 ? "" : "s"} with new messages.`}
      </div>
      <div className="grid">
        <Card title={`Conversations (${threads?.length ?? 0})`}>
          {threads === null ? <div className="empty">Loading…</div> : threads.length === 0
            ? <div className="empty">No patient has written yet.</div>
            : threads.map((t) => (
              <div className="appt" key={t.id}>
                <div className="info">
                  <b>{t.subject || "(no subject)"}</b>
                  <small><Patient name={t.patient_name} id={t.patient} /> - last activity {when(t.last_message_at)}</small>
                </div>
                {(t.unread_for_me ?? t.unread) > 0 && <Pill kind="warn">{t.unread_for_me ?? t.unread} new</Pill>}
                {t.status === "closed" && <Pill kind="info">Closed</Pill>}
                <button className="btn ghost" onClick={() => setOpen(open === t.id ? "" : t.id)}>{open === t.id ? "Hide" : "Open"}</button>
              </div>
            ))}
        </Card>
        {open && <Conversation id={open} onChanged={load} />}
      </div>
    </>
  );
}
