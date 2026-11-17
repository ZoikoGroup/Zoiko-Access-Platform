import { useEffect, useState } from "react";
import { ApiError, getMyNote, getMyNotes, type MyNote } from "../api";
import { Card, Pill } from "../ui";

export function MyNotes() {
  const [rows, setRows] = useState<MyNote[] | null>(null);
  const [open, setOpen] = useState<MyNote | null>(null);
  const [error, setError] = useState("");
  const [off, setOff] = useState(false);
  useEffect(() => {
    getMyNotes().then((d) => { setRows(d.notes); if (d.enabled === false) setOff(true); }).catch((e: unknown) => {
      if (e instanceof ApiError && e.code === "NOT_FOUND") setOff(true);
      else setError(e instanceof Error ? e.message : "Could not load");
      setRows([]);
    });
  }, []);
  async function show(id: string) {
    setError("");
    try { setOpen(await getMyNote(id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the note"); }
  }
  return (
    <>
      <div className="hero"><div>
        <h1>My Notes</h1>
        <p>Notes your doctor has written about your visits and signed. A note appears here a day after it is signed, so your doctor can follow up with you first. A note is never changed after it is signed: if it is corrected, the earlier version stays and is marked as corrected.</p>
      </div></div>
      {off && <div className="notice" role="status">Notes are not available yet.</div>}
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Notes (${rows?.length ?? 0})`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">No notes to show yet.</div>
            : rows.map((n) => (
              <div className="appt" key={n.id}>
                <div className="info">
                  <b>Visit on {n.note_date} {n.corrected && <Pill kind="info">Corrected later</Pill>}</b>
                  <small>Written by {n.author_name || "your doctor"} · version {n.version}{n.amend_reason ? ` (${n.amend_reason.replace(/_/g, " ")})` : ""}</small>
                </div>
                <button className="btn ghost" onClick={() => void show(n.id)}>Read</button>
              </div>
            ))}
        </Card>
        {open && (
          <Card title={`Visit on ${open.note_date}`}>
            <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }} aria-label="Note text">{open.body}</pre>
            {open.addenda && open.addenda.length > 0 && (
              <div role="region" aria-label="Addenda" style={{ marginTop: 8 }}>
                <b>Added later by your doctor</b>
                {open.addenda.map((a) => (
                  <div key={a.id} style={{ marginTop: 6 }}>
                    <small>{a.added_at.slice(0, 10)}</small>
                    <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{a.body}</pre>
                  </div>
                ))}
              </div>
            )}
            <small style={{ display: "block", marginTop: 8 }}>Opening a note is recorded in your privacy log.</small>
            <div style={{ marginTop: 8 }}><button className="btn ghost" onClick={() => setOpen(null)}>Close</button></div>
          </Card>
        )}
      </div>
    </>
  );
}
