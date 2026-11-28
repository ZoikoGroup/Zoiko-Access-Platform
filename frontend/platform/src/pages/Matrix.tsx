import { useCallback, useEffect, useState } from "react";
import { ApiError, getMatrix, importMatrixSeed, type MatrixData } from "../api";
import { Card, Pill } from "../ui";

const STANDING: Record<string, { text: string; kind: "ok" | "warn" | "bad" }> = {
  met: { text: "Met", kind: "ok" }, partly: { text: "Partly", kind: "warn" }, not_built: { text: "Not built", kind: "bad" },
};

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
          {data?.requirements.map((r) => (
            <div key={r.public_id} className="appt"><div className="info">
              <b>{r.source} {r.req_id} <Pill kind={STANDING[r.status].kind}>{STANDING[r.status].text}</Pill></b>
              <small>{r.title}</small>
              {r.evidence && <small>{r.evidence}</small>}
            </div></div>
          ))}
        </Card>
      </div>
    </>
  );
}
