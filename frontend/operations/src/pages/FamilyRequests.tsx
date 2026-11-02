import { useCallback, useEffect, useState } from "react";
import { approveFamily, listFamilyRequests, rejectFamily, type Staff } from "../api";
import type { FamilyRequest } from "../types";
import { Card } from "../ui";
import { NoStaff } from "./Appointments";

const RELATIONSHIP: Record<string, string> = { parent: "parent", guardian: "legal guardian", caregiver: "caregiver" };

function Request({ r, onDone }: { r: FamilyRequest; onDone: () => Promise<void> }) {
  const [checked, setChecked] = useState(false);
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  return (
    <div className="appt">
      <div className="info">
        <b>{r.name || "Unnamed"}</b>
        <small>Born {r.birth_date || "unknown"}. Asked by {r.requested_by_name ?? r.requested_by} as their {RELATIONSHIP[r.relationship] ?? r.relationship}.</small>
        <small>Asked on {r.requested_at.slice(0, 10)}</small>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          I have checked proof of identity and proof of authority for this request
        </label>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          Allow until (optional)
          <input type="date" value={until} onChange={(e) => setUntil(e.target.value)}
            style={{ padding: "6px 8px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg)" }} />
        </label>
        {error && <small style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      <span>
        <button className="btn" disabled={busy || !checked} onClick={() => void run(() => approveFamily(r.id, until))}>Approve</button>{" "}
        <button className="btn ghost" disabled={busy} onClick={() => void run(() => rejectFamily(r.id))}>Reject</button>
      </span>
    </div>
  );
}

/** Guardians waiting for approval. The clinic checks documents at the desk, then ticks the box and approves. */
export function FamilyRequests({ staff }: { staff: Staff | null }) {
  const [rows, setRows] = useState<FamilyRequest[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!staff) return;
    try { setRows(await listFamilyRequests()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the requests"); }
  }, [staff]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  if (!staff) return <NoStaff />;
  return (
    <>
      <div className="hero"><div>
        <h1>Family requests</h1>
        <p>Patients asking to act for a child or someone they care for. Check their proof of identity and authority at the desk, tick the box, then approve. Approval lets them see and book for that person, never change their details.</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card title={`${rows?.length ?? 0} waiting`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">No requests are waiting.</div>
            : rows.map((r) => <Request key={r.id} r={r} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
