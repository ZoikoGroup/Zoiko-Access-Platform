import { useCallback, useEffect, useState } from "react";
import { codeMapStep, getCodeMaps, type CodeMapping } from "../api";
import { Card, Pill } from "../ui";

type PillKind = "ok" | "warn" | "bad" | "info";
const STATE: Record<CodeMapping["state"], { text: string; kind: PillKind }> = {
  unmapped: { text: "Needs review", kind: "warn" },
  proposed: { text: "Match proposed", kind: "info" },
  approved: { text: "Approved", kind: "ok" },
  rejected: { text: "Rejected", kind: "bad" },
};
const REASONS: [string, string][] = [
  ["no_equivalent", "No SNOMED CT equivalent"], ["wrong_match", "The proposed match is wrong"], ["other", "Other"],
];

function Row({ m, me, canPropose, canDecide, onDone }: { m: CodeMapping; me: string; canPropose: boolean; canDecide: boolean; onDone: () => Promise<void> }) {
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("no_equivalent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function act(step: "propose" | "approve" | "reject") {
    setBusy(true); setError("");
    try { await codeMapStep(m.id, step, step === "propose" ? { target_code: target.trim() } : step === "reject" ? { reason } : {}); await onDone(); }
    catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? "That is not a valid SNOMED CT concept id." : code === "POLICY_DENIED" ? "Someone else must decide this." : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }
  const s = STATE[m.state];
  const mine = m.proposed_by === me;
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{m.source_code} {m.source_display && `(${m.source_display})`} <Pill kind={s.kind}>{s.text}</Pill></b>
        <small>{m.source_system} · seen {m.seen_count} time{m.seen_count === 1 ? "" : "s"}{m.target_code ? ` · SNOMED CT ${m.target_code}` : ""}{m.proposed_by_name ? ` · proposed by ${m.proposed_by_name}` : ""}{m.decided_by_name ? ` · decided by ${m.decided_by_name}` : ""}</small>
        {m.reject_reason && <small>Reason: {m.reject_reason.replace(/_/g, " ")}</small>}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {canPropose && (m.state === "unmapped" || m.state === "rejected") && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input className="field" aria-label={`SNOMED CT concept for ${m.source_code}`} placeholder="SNOMED CT concept id" value={target} onChange={(e) => setTarget(e.target.value)} />
          <button className="btn" disabled={busy || target.trim().length < 6} onClick={() => void act("propose")}>Propose this match</button>
        </div>
      )}
      {canDecide && (m.state === "proposed" || m.state === "unmapped") && !mine && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {m.state === "proposed" && <button className="btn" disabled={busy} onClick={() => void act("approve")}>Approve</button>}
          <select aria-label={`Reason for rejecting ${m.source_code}`} value={reason} onChange={(e) => setReason(e.target.value)}>
            {REASONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <button className="btn ghost" disabled={busy} onClick={() => void act("reject")}>Reject</button>
        </div>
      )}
      {canDecide && mine && m.state === "proposed" && <small>You proposed this; another safety officer must decide.</small>}
    </div>
  );
}

/** Partners' local codes and their SNOMED CT match (DEV-322). Regional support proposes; a
 * different person, the clinical safety officer, approves. Until then the code is never guessed. */
export function CodeMaps() {
  const [data, setData] = useState<{ mappings: CodeMapping[]; me: string; can_propose: boolean; can_decide: boolean } | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await getCodeMaps()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const waiting = (data?.mappings ?? []).filter((m) => m.state === "unmapped" || m.state === "proposed").length;
  return (
    <>
      <div className="hero"><div>
        <h1>Code mapping</h1>
        <p>Local codes that partners sent next to SNOMED CT. Regional support proposes what each one means; a different person, the clinical safety officer, approves or rejects it. Until then the code is never guessed, and every message is kept as it arrived. {waiting > 0 ? `${waiting} waiting.` : ""}</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Codes (${data?.mappings.length ?? 0})`}>
          {data === null ? <div className="empty">Loading…</div> : data.mappings.length === 0
            ? <div className="empty">No local codes have arrived yet.</div>
            : data.mappings.map((m) => <Row key={m.id} m={m} me={data.me} canPropose={data.can_propose} canDecide={data.can_decide} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
