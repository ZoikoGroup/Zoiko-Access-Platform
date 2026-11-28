import { useEffect, useState } from "react";
import { getMyProgrammes, joinProgramme, leaveProgramme, type MyProgramme } from "../api";
import { Card } from "../ui";

export function MyProgramme() {
  const [rows, setRows] = useState<MyProgramme[] | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const load = () => getMyProgrammes().then(setRows).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  useEffect(() => { void load(); }, []);
  async function join() {
    setBusy(true); setError(""); setDone("");
    try { const p = await joinProgramme(code); setCode(""); setDone(`You are now counted in ${p.employer}'s programme.`); await load(); }
    catch (e) {
      const c = (e as { code?: string }).code;
      setError(c === "NOT_FOUND" ? "That code did not work. Check it, or ask your employer for a new one."
        : c === "CONFLICT_VERSION" ? "You are already part of that employer's programme."
        : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }
  async function leave(id: string) {
    setBusy(true); setError(""); setDone("");
    try { await leaveProgramme(id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <>
      <div className="hero"><div>
        <h1>Employer Programme</h1>
        <p>If your employer runs a health programme, you can choose to be counted in it. Your employer is then told only how many appointments and visits all the people who joined had each month, and only when enough people joined that nobody can be picked out. It is never told who you are, which clinic you use or what you had done. You can leave at any time.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">{done}</div>}
      <div className="grid">
        <Card title="Join with a code">
          <label>The one-time code your employer gave you
            <input className="field" value={code} onChange={(e) => setCode(e.target.value)} placeholder="XXXXX-XXXXX" maxLength={14} />
          </label>
          <div style={{ marginTop: 8 }}><button className="btn" disabled={busy || code.trim().length < 10} onClick={() => void join()}>I agree to be counted</button></div>
        </Card>
        <Card title={`Programmes you are in (${rows?.length ?? 0})`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">You are not in any programme.</div>
            : rows.map((p) => (
              <div className="appt" key={p.id}>
                <div className="info"><b>{p.employer}</b><small>Joined {p.linked_at?.slice(0, 10)}</small></div>
                <button className="btn ghost" disabled={busy} onClick={() => void leave(p.id)}>Leave</button>
              </div>
            ))}
        </Card>
      </div>
    </>
  );
}
