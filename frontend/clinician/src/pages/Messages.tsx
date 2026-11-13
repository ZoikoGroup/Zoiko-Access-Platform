import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getThread, getThreads, replyToThread } from "../api";
import type { Thread, ThreadView } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

// One key for one reply: sending the same words twice (a double tap, a retry) posts once.
const newKey = () => crypto.randomUUID().replace(/-/g, "") + "k";

function Conversation({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [view, setView] = useState<ThreadView | null>(null);
  const [text, setText] = useState("");
  const key = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setView(await getThread(id)); setError(""); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, [id]);                                           // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setView(null); void load(); }, [load]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    key.current ??= newKey();
    try { await replyToThread(id, text, key.current); key.current = null; setText(""); await load(); }
    catch (err) {
      setError(err instanceof ApiError && err.code === "CONFLICT_VERSION" ? "This conversation is closed."
        : err instanceof ApiError && err.code === "RATE_LIMITED" ? "Please wait a little before sending more."
        : err instanceof ApiError && err.code === "POLICY_DENIED" ? "You can only reply while you are treating this patient (not when following up read only)."
        : err instanceof Error ? err.message : "Could not send");
    } finally { setBusy(false); }
  }

  if (!view) return <Card title="Conversation">{error ? <div className="error" role="alert">{error}</div> : <div className="empty">Loading…</div>}</Card>;
  return (
    <Card title={view.subject} sub={`${view.patient_name || view.patient} · ${view.status === "closed" ? "Closed" : "Open"}`}>
      {view.messages.map((m) => (
        <div key={m.id} className="appt" style={{ alignItems: "flex-start" }}>
          <div className="info">
            <b>{m.mine ? "You" : m.side === "staff" ? (m.from || "The clinic") : view.patient_name || "The patient"}</b>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.body || "(this message cannot be shown)"}</div>
            <small>{when(m.at)}</small>
          </div>
        </div>
      ))}
      {view.status === "open" && (
        <form className="ref-form" onSubmit={(e) => void send(e)} style={{ marginTop: 12 }}>
          <label>Your reply
            <textarea value={text} maxLength={2000} onChange={(e) => { key.current = null; setText(e.target.value); }} placeholder="Write a reply" />
          </label>
          <small>The patient sees this as a reply from the clinic. Messages are for appointments and everyday questions, not emergencies.</small>
          {error && <div className="error" role="alert">{error}</div>}
          <div><button className="btn" type="submit" disabled={busy || !text.trim()}>{busy ? "Sending…" : "Reply"}</button></div>
        </form>
      )}
    </Card>
  );
}

function List({ threads, open, onOpen }: { threads: Thread[]; open: string; onOpen: (id: string) => void }) {
  if (threads.length === 0) return <div className="empty">No conversations with your patients yet.</div>;
  return (
    <>
      {threads.map((t) => {
        const unread = t.unread_for_me ?? t.unread;
        return (
          <div className="appt" key={t.id}>
            <div className="info">
              <b>{t.subject || "(no subject)"}</b>
              <small>{t.patient_name || t.patient} · last activity {when(t.last_message_at)}</small>
            </div>
            {unread > 0 && <Pill kind="warn">{unread} new</Pill>}
            {t.status === "closed" && <Pill kind="info">Closed</Pill>}
            <button className="btn ghost" onClick={() => onOpen(open === t.id ? "" : t.id)}>{open === t.id ? "Hide" : "Open"}</button>
          </div>
        );
      })}
    </>
  );
}

export function Messages() {
  const result = useLoad(getThreads, 30_000);
  const [open, setOpen] = useState("");
  const waiting = (result.data ?? []).filter((t) => (t.unread_for_me ?? t.unread) > 0).length;
  return (
    <>
      <div className="hero"><div>
        <h1>Messages</h1>
        <p>Conversations with patients you care for. You see only their threads, and every look is recorded with the reason you chose. {waiting > 0 ? `${waiting} with new messages.` : ""}</p>
      </div></div>
      {/* Said aloud by a screen reader whenever the number changes (GHE-16 TC34, DEV-162). */}
      <div className="sr-only" role="status" aria-live="polite" data-testid="inbox-status">
        {result.data === null ? "" : waiting === 0 ? "No conversations waiting." : `${waiting} conversation${waiting === 1 ? "" : "s"} with new messages.`}
      </div>
      <div className="grid">
        <Card title={`Conversations (${result.data?.length ?? 0})`}>
          <Gate result={result}>{(rows) => <List threads={rows} open={open} onOpen={setOpen} />}</Gate>
        </Card>
        {open && <Conversation id={open} onChanged={() => undefined} />}
      </div>
    </>
  );
}
