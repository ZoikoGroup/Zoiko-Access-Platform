import { useCallback, useEffect, useState } from "react";
import { ApiError, getThread, getThreads, replyToThread, startThread, type MessageThread, type ThreadView } from "../api";
import { Card, Pill } from "../ui";

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function NewMessage({ onSent }: { onSent: (id: string) => Promise<void> }) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { const id = await startThread({ subject, body }); setSubject(""); setBody(""); await onSent(id); }
    catch (err) {
      setError(err instanceof ApiError && err.status === 429 ? "You have sent a lot of messages in the last hour. Please wait a little."
        : err instanceof ApiError && err.status === 422 ? "A subject and a message are needed (the message can be up to 2000 characters)."
        : err instanceof Error ? err.message : "Could not send");
    } finally { setBusy(false); }
  }

  return (
    <form className="profile-form" onSubmit={(e) => void submit(e)}>
      <label>Subject
        <input value={subject} maxLength={120} onChange={(e) => setSubject(e.target.value)} required />
      </label>
      <label>Your message
        <textarea value={body} maxLength={2000} onChange={(e) => setBody(e.target.value)} required
          style={{ minHeight: 110, padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }} />
      </label>
      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn" type="submit" disabled={busy || !subject.trim() || !body.trim()}>{busy ? "Sending…" : "Send to the clinic"}</button></div>
    </form>
  );
}

function Conversation({ id, onChanged }: { id: string; onChanged: () => Promise<void> }) {
  const [view, setView] = useState<ThreadView | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setView(await getThread(id)); setError(""); await onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, [id]);                                           // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setView(null); void load(); }, [load]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await replyToThread(id, text); setText(""); await load(); }
    catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? "The clinic has closed this conversation. Start a new message."
        : err instanceof ApiError && err.status === 429 ? "Please wait a little before sending more."
        : err instanceof Error ? err.message : "Could not send");
    } finally { setBusy(false); }
  }

  if (!view) return <Card title="Conversation">{error ? <div className="error">{error}</div> : <div className="empty">Loading…</div>}</Card>;
  return (
    <Card title={view.subject} sub={view.status === "closed" ? "Closed by the clinic" : "Open"}>
      {view.messages.map((m) => (
        <div key={m.id} className="appt" style={{ alignItems: "flex-start" }}>
          <div className="info">
            <b>{m.mine ? "You" : m.side === "staff" ? "The clinic" : "Someone you act for"}</b>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.body || "(this message cannot be shown)"}</div>
            <small>{when(m.at)}</small>
            {m.side === "patient" && (
              <small>{m.seen_at ? `Opened by the clinic ${when(m.seen_at)}. That means a person opened it, not that it has been answered.` : "Sent. The clinic has not opened it yet."}</small>
            )}
          </div>
        </div>
      ))}
      {view.status === "open" && (
        <form className="profile-form" onSubmit={(e) => void send(e)} style={{ marginTop: 12 }}>
          <textarea value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} aria-label="Your reply"
            placeholder="Write a reply" style={{ minHeight: 80, padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }} />
          {error && <div className="error" role="alert">{error}</div>}
          <div><button className="btn" type="submit" disabled={busy || !text.trim()}>{busy ? "Sending…" : "Reply"}</button></div>
        </form>
      )}
    </Card>
  );
}

export function Messages({ onChanged }: { onChanged: () => Promise<void> }) {
  const [threads, setThreads] = useState<MessageThread[] | null>(null);
  const [open, setOpen] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setThreads(await getThreads()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function sent(id: string) { await load(); setOpen(id); await onChanged(); }

  return (
    <>
      <div className="hero"><div>
        <h1>Messages</h1>
        <p>Write to your clinic about appointments and everyday questions. Messages are stored encrypted. <b>This is not for emergencies or urgent medical problems: call your local emergency number.</b></p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card title={`Conversations (${threads?.length ?? 0})`}>
          {threads === null ? <div className="empty">Loading…</div> : threads.length === 0
            ? <div className="empty">No messages yet.</div>
            : threads.map((t) => (
              <div className="appt" key={t.id}>
                <div className="info">
                  <b>{t.subject || "(no subject)"}</b>
                  <small>Last activity {when(t.last_message_at)}</small>
                </div>
                {(t.unread_for_me ?? t.unread) > 0 && <Pill kind="warn">{t.unread_for_me ?? t.unread} new</Pill>}
                {t.status === "closed" && <Pill kind="info">Closed</Pill>}
                <button className="btn ghost" onClick={() => setOpen(open === t.id ? "" : t.id)}>{open === t.id ? "Hide" : "Open"}</button>
              </div>
            ))}
        </Card>
        {open && <Conversation id={open} onChanged={async () => { await load(); await onChanged(); }} />}
        <Card title="New message"><NewMessage onSent={sent} /></Card>
      </div>
    </>
  );
}
