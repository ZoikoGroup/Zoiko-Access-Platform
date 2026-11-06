import { useCallback, useEffect, useState } from "react";
import { ApiError, addRelease, getReleases, type ReleaseRow } from "../api";
import { Card } from "../ui";

function today(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "CONFLICT_VERSION" ? "That version is already in the log. Entries are never edited: record a new version instead."
    : code === "VALIDATION_FAILED" ? "Check the form: version up to 40 characters, summary up to 200, details up to 2000, and a date that is not in the future."
    : code === "POLICY_DENIED" ? "Your account may not add releases."
    : e instanceof Error ? e.message : "That did not work";
}

/** The platform's hand-written release log: what went live, when, and what changed. It deploys nothing. */
export function Releases({ canAdd }: { canAdd: boolean }) {
  const [rows, setRows] = useState<ReleaseRow[] | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ version: "", summary: "", released_on: today(), details: "" });

  const load = useCallback(async () => {
    try { setRows(await getReleases()); setError(""); }
    catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setDone("");
    try {
      await addRelease(form);
      setDone(`${form.version.trim()} was added to the log.`);
      setForm({ version: "", summary: "", released_on: today(), details: "" });
      await load();
    } catch (err) { setError(friendly(err)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="hero">
        <div><h1>Releases</h1><p>A log, written by hand, of what went live and when. It deploys nothing and cannot be edited: a wrong entry is answered by a new one.</p></div>
      </div>
      <div className="grid">
        {canAdd && (
          <Card title="Record a release" sub="Needs support rights. Each entry is also written to the platform audit log.">
            <form className="add-form" onSubmit={(e) => void submit(e)}>
              <label>Version
                <input value={form.version} maxLength={40} onChange={(e) => setForm({ ...form, version: e.target.value })} required />
              </label>
              <label>Went live on
                <input type="date" value={form.released_on} max={today()} onChange={(e) => setForm({ ...form, released_on: e.target.value })} required />
              </label>
              <label>Summary (one line)
                <input value={form.summary} maxLength={200} onChange={(e) => setForm({ ...form, summary: e.target.value })} required />
              </label>
              <label>Details (optional)
                <textarea value={form.details} maxLength={2000} rows={4} onChange={(e) => setForm({ ...form, details: e.target.value })} />
              </label>
              {error && <div className="error" role="alert">{error}</div>}
              {done && <div className="success" role="status">{done}</div>}
              <div><button className="btn" type="submit" disabled={busy || !form.version.trim() || !form.summary.trim() || !form.released_on}>
                {busy ? "Saving…" : "Add to the log"}
              </button></div>
            </form>
          </Card>
        )}
        <Card title={`${rows?.length ?? 0} release${rows?.length === 1 ? "" : "s"}`} sub="Newest first">
          {!canAdd && error && <div className="error" role="alert">{error}</div>}
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">Nothing recorded yet.</div> : rows.map((r) => (
            <div key={r.id} className="appt">
              <div className="info">
                <b>{r.version} <span className="sub">- {r.released_on}</span></b>
                <span>{r.summary}</span>
                {r.details && <span className="sub" style={{ whiteSpace: "pre-wrap" }}>{r.details}</span>}
                <span className="sub">Recorded by {r.recorded_by}</span>
              </div>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
