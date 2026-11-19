import { useCallback, useEffect, useState } from "react";
import { ApiError, addNoteAddendum, amendNote, createNote, editNote, getNote, getNotes, getPatients, setNoteGuardianHidden, signNote } from "../api";
import type { Note, PatientRow } from "../types";
import { Card, Pill } from "../ui";

const STATE: Record<Note["state"], { text: string; kind: "ok" | "warn" | "info" }> = {
  draft: { text: "Draft, not signed", kind: "warn" },
  signed: { text: "Signed", kind: "ok" },
  superseded: { text: "Replaced by a later version", kind: "info" },
};
const REASONS: [string, string][] = [
  ["clarification", "Clarification"], ["new_information", "New information"], ["entered_in_error", "Entered in error"],
];
const today = () => new Date().toISOString().slice(0, 10);

function Open({ id, onDone }: { id: string; onDone: (openId?: string) => void }) {
  const [note, setNote] = useState<Note | null>(null);
  const [text, setText] = useState("");
  const [reason, setReason] = useState("clarification");
  const [addendum, setAddendum] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    getNote(id).then((n) => { setNote(n); setText(n.body ?? ""); }).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not open"));
  }, [id]);
  async function run(action: () => Promise<Note>, then?: (n: Note) => void) {
    setBusy(true); setError("");
    try { const n = await action(); then ? then(n) : onDone(id); }
    catch (e) { setError(e instanceof ApiError && e.code === "CONFLICT_VERSION" ? "That cannot be done to this note now." : e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  if (error && !note) return <div className="error" role="alert">{error}</div>;
  if (!note) return <div className="empty">Opening…</div>;
  const state = STATE[note.state];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>Note of {note.note_date} · version {note.version} <Pill kind={state.kind}>{state.text}</Pill></b>
        <small>Written by {note.author_name || "a colleague"}{note.signed_at ? `, signed ${note.signed_at.slice(0, 10)}` : ""}. Opening it was recorded.</small>
        {note.amend_reason && <small>A correction of an earlier version ({note.amend_reason.replace(/_/g, " ")}).</small>}
        {note.state === "superseded" && <small>This version was replaced; it is kept as it was signed.</small>}
        {note.erased && <small>The text was erased when the NHS retention period ended; the record that it existed stays.</small>}
      </div>
      {note.state === "draft" && note.mine ? (
        <>
          <label>Note
            <textarea className="field" rows={8} value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} />
          </label>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn ghost" disabled={busy || !text.trim()} onClick={() => void run(() => editNote(id, text))}>Save draft</button>
            <button className="btn" disabled={busy || !text.trim()} onClick={() => void run(async () => { await editNote(id, text); return signNote(id); })}>Sign this note</button>
          </div>
          <small>Once signed it cannot be changed. A correction is a new version.</small>
        </>
      ) : (
        <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }} aria-label="Note text">{note.body}</pre>
      )}
      {note.state === "signed" && note.mine && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <label>Why correct it?{" "}
            <select value={reason} onChange={(e) => setReason(e.target.value)}>
              {REASONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <button className="btn ghost" disabled={busy} onClick={() => void run(() => amendNote(id, reason), (n) => onDone(n.id))}>Start a correction</button>
        </div>
      )}
      {note.addenda && note.addenda.length > 0 && (
        <div role="region" aria-label="Addenda">
          <b>Addenda</b>
          {note.addenda.map((a) => (
            <div key={a.id} style={{ marginTop: 6 }}>
              <small>Added {a.added_at.slice(0, 10)}</small>
              <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{a.body}</pre>
            </div>
          ))}
        </div>
      )}
      {note.state === "signed" && note.mine && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label>Add an addendum (the signed note stays as it is)
            <textarea className="field" rows={3} maxLength={5000} value={addendum} onChange={(e) => setAddendum(e.target.value)} style={{ width: "100%" }} />
          </label>
          <div><button className="btn ghost" disabled={busy || !addendum.trim()} onClick={() => void run(async () => { const n = await addNoteAddendum(id, addendum); setAddendum(""); return n; }, (n) => setNote({ ...n, body: note.body }))}>Add the addendum</button></div>
        </div>
      )}
      {note.state !== "superseded" && note.mine && (
        <label>
          <input type="checkbox" checked={!!note.guardian_hidden} disabled={busy}
            onChange={(e) => { const hidden = e.target.checked; void run(() => setNoteGuardianHidden(id, hidden), (n) => setNote({ ...note, guardian_hidden: n.guardian_hidden })); }} />{" "}
          Keep this note from a parent or guardian (the patient still sees it)
        </label>
      )}
      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn ghost" onClick={() => onDone()}>Close</button></div>
    </div>
  );
}

export function Notes() {
  const [patients, setPatients] = useState<PatientRow[] | null>(null);
  const [person, setPerson] = useState("");
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [openId, setOpenId] = useState("");
  const [writing, setWriting] = useState(false);
  const [day, setDay] = useState(today());
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [off, setOff] = useState(false);

  useEffect(() => { getPatients().then(setPatients).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load")); }, []);
  const load = useCallback(async (p: string) => {
    if (!p) { setNotes(null); return; }
    try { setNotes(await getNotes(p)); setError(""); setOff(false); }
    catch (e) {
      if (e instanceof ApiError && e.code === "NOT_FOUND") setOff(true);
      else setError(e instanceof ApiError && e.code === "POLICY_DENIED" ? "You can only open notes while you are treating this patient." : e instanceof Error ? e.message : "Could not load");
      setNotes(null);
    }
  }, []);
  useEffect(() => { void load(person); }, [person, load]);

  async function start() {
    setBusy(true); setError("");
    try { const n = await createNote(person, day, text); setWriting(false); setText(""); await load(person); setOpenId(n.id); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="hero"><div>
        <h1>Notes</h1>
        <p>Write, sign and correct consultation notes for your own patients. The text is encrypted. A signed note is never edited: a correction is a new version, and the original stays. Every time you open a note it is recorded.</p>
      </div></div>
      {off && <div className="notice" role="status">Notes are switched off for now. Ask your administrator.</div>}
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title="Patient">
          <label>Patient{" "}
            <select value={person} onChange={(e) => { setPerson(e.target.value); setOpenId(""); setWriting(false); }}>
              <option value="">Choose a patient</option>
              {(patients ?? []).map((p) => <option key={p.id} value={p.id}>{p.patient_name || p.id}</option>)}
            </select>
          </label>
          {person && !off && !writing && <div style={{ marginTop: 8 }}><button className="btn" onClick={() => setWriting(true)}>Write a new note</button></div>}
          {writing && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
              <label>Date of the consultation <input className="field" type="date" value={day} max={today()} onChange={(e) => setDay(e.target.value)} /></label>
              <label>Note
                <textarea className="field" rows={8} value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} />
              </label>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" disabled={busy || !text.trim()} onClick={() => void start()}>Save as draft</button>
                <button className="btn ghost" onClick={() => setWriting(false)}>Cancel</button>
              </div>
            </div>
          )}
        </Card>
        {person && !off && (
          <Card title={`Notes (${notes?.length ?? 0})`}>
            {notes === null ? <div className="empty">Loading…</div> : notes.length === 0
              ? <div className="empty">No notes for this patient yet.</div>
              : notes.map((n) => (
                <div className="appt" key={n.id}>
                  <div className="info">
                    <b>{n.note_date} · version {n.version} <Pill kind={STATE[n.state].kind}>{STATE[n.state].text}</Pill></b>
                    <small>{n.mine ? "Written by you" : `Written by ${n.author_name || "a colleague"}`}</small>
                  </div>
                  <button className="btn ghost" onClick={() => setOpenId(n.id)}>Open</button>
                </div>
              ))}
          </Card>
        )}
        {openId && <Card title="Note"><Open key={openId} id={openId} onDone={(next) => { setOpenId(next ?? ""); void load(person); }} /></Card>}
      </div>
    </>
  );
}
