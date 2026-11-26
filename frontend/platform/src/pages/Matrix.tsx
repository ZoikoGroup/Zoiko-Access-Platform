import { useCallback, useEffect, useState } from "react";
import { ApiError, getMatrix, importMatrixSeed, updateRequirement, type MatrixData } from "../api";
import { Card, Pill } from "../ui";

const STANDING: Record<string, { text: string; kind: "ok" | "warn" | "bad" }> = {
  met: { text: "Met", kind: "ok" }, partly: { text: "Partly", kind: "warn" }, not_built: { text: "Not built", kind: "bad" },
};

type Requirement = MatrixData["requirements"][number];

/** One requirement, and (for regional support) changing how far it is met. */
function Row({ r, canWrite, onDone }: { r: Requirement; canWrite: boolean; onDone: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [status, setStatus] = useState<string>(r.status);
  const [evidence, setEvidence] = useState(r.evidence);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await updateRequirement(r.public_id, status, evidence); setEditing(false); await onDone(); }
    catch (err) {
      const code = err instanceof ApiError ? err.code : "";
      setError(code === "VALIDATION_FAILED" ? "To mark it “met”, the evidence must name an automated test, such as tests/test_booking.py. Evidence can be up to 1000 characters."
        : code === "POLICY_DENIED" ? "Your account may not change requirements."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{r.source} {r.req_id} <Pill kind={STANDING[r.status].kind}>{STANDING[r.status].text}</Pill></b>
        <small>{r.title}</small>
        {r.evidence && <small>{r.evidence}</small>}
      </div>
      {canWrite && !editing && (
        <button className="btn ghost" onClick={() => { setStatus(r.status); setEvidence(r.evidence); setEditing(true); }}>
          Update<span className="sr-only"> {r.source} {r.req_id}</span>
        </button>
      )}
      {editing && (
        <form className="add-form" style={{ flexBasis: "100%" }} onSubmit={(e) => void save(e)}>
          <label>Standing of {r.source} {r.req_id}
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="met">Met</option><option value="partly">Partly</option><option value="not_built">Not built</option>
            </select>
          </label>
          <label>Evidence
            <textarea value={evidence} maxLength={1000} onChange={(e) => setEvidence(e.target.value)} />
          </label>
          {error && <div className="error" role="alert">{error}</div>}
          <div className="row">
            <button className="btn" type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
            <button type="button" className="btn ghost" onClick={() => { setEditing(false); setError(""); }}>Close</button>
          </div>
        </form>
      )}
    </div>
  );
}

/** The requirements matrix: each requirement of the specifications and how far it is met. */
export function Matrix({ canWrite }: { canWrite: boolean }) {
  const [data, setData] = useState<MatrixData | null>(null);
  const [source, setSource] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setData(await getMatrix(source, status)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, [source, status]);
  useEffect(() => { void load(); }, [load]);

  async function seed() {
    setBusy(true); setError(""); setDone("");
    try {
      const c = await importMatrixSeed();
      setDone(`Loaded: ${c.added} added, ${c.changed} changed, ${c.unchanged} already there.`);
      await load();
    } catch (e) { setError(e instanceof ApiError && e.code === "POLICY_DENIED" ? "Your account may not load requirements." : e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  const sources = data ? Object.keys(data.summary) : [];
  return (
    <>
      <div className="hero"><div>
        <h1>Requirements matrix</h1>
        <p>Each requirement taken from the specifications, how far it is met, and the evidence. A requirement can only be marked "met" if its evidence names an automated test. These are the developer's own readings, not a run of the specifications' tests, and nobody else has reviewed them.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">{done}</div>}
      <div className="grid">
        <Card title="How far each specification is met" action={canWrite ? <button className="btn ghost" disabled={busy} onClick={() => void seed()}>Load the developer's readings</button> : undefined}>
          {!data ? <div className="empty">Loading…</div> : sources.length === 0 ? <div className="empty">Nothing recorded yet.</div> : sources.map((s) => {
            const c = data.summary[s];
            return (
              <div key={s} className="appt"><div className="info">
                <b>{s}</b>
                <small>{c.total} requirements: {c.met} met, {c.partly} partly, {c.not_built} not built ({Math.round((c.met / c.total) * 100)}% met).</small>
              </div></div>
            );
          })}
        </Card>
        <Card title={`Requirements (${data?.requirements.length ?? 0})`}>
          <div className="form-row">
            <label>Specification
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="">All</option>{sources.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label>Standing
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">All</option><option value="met">Met</option><option value="partly">Partly</option><option value="not_built">Not built</option>
              </select>
            </label>
          </div>
          {data?.requirements.map((r) => <Row key={r.public_id} r={r} canWrite={canWrite} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
