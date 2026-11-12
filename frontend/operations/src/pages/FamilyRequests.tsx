import { useCallback, useEffect, useState } from "react";
import { approveFamily, familyStep, listFamilyRequests, rejectFamily, type Staff } from "../api";
import type { FamilyRequest } from "../types";
import { Card, Pill } from "../ui";
import { NoStaff } from "./Appointments";

// Where a request or link stands (GHE-06's relationship-grant statuses).
const STATUS: Record<string, { text: string; kind: "ok" | "warn" | "info" | "bad" }> = {
  proposed: { text: "New request", kind: "warn" },
  proof_required: { text: "Waiting for proof", kind: "info" },
  approval_pending: { text: "Proof seen: ready to decide", kind: "info" },
  active: { text: "Approved", kind: "ok" },
  suspended: { text: "Paused", kind: "bad" },
  expired: { text: "Ran out", kind: "bad" },
};

const RELATIONSHIP: Record<string, string> = { parent: "parent", guardian: "legal guardian", caregiver: "caregiver" };
// What a guardian can be allowed to do. Medical records are not on this list: they stay closed.
const SCOPES: [string, string][] = [
  ["appointments", "Book, change and see appointments"],
  ["messages", "Send and read messages with the clinic"],
  ["referrals", "See referrals"],
  ["insurance", "See and manage insurance"],
];

const PROOF_KINDS: [string, string][] = [
  ["birth_record", "Birth record"], ["adoption_record", "Adoption record"], ["court_order", "Court order"],
  ["power_of_attorney", "Power of attorney"], ["carer_registration", "Carer registration"],
  ["patient_written_consent", "The patient's written consent"], ["other_document", "Another document"],
];

function Request({ r, onDone }: { r: FamilyRequest; onDone: () => Promise<void> }) {
  const [checked, setChecked] = useState(false);
  const [until, setUntil] = useState("");
  const [scopes, setScopes] = useState<string[]>(["appointments"]);   // the minimum to start with
  const [proofKind, setProofKind] = useState("");
  const [proofRef, setProofRef] = useState("");
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
        <b>{r.name || "Unnamed"} <Pill kind={STATUS[r.status]?.kind ?? "info"}>{STATUS[r.status]?.text ?? r.status}</Pill></b>
        <small>Born {r.birth_date || "unknown"}. Asked by {r.requested_by_name ?? r.requested_by} as their {RELATIONSHIP[r.relationship] ?? r.relationship}.</small>
        <small>Asked on {r.requested_at.slice(0, 10)}</small>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          I have checked proof of identity and proof of authority for this request
        </label>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          What proof did you see?
          <select value={proofKind} onChange={(e) => setProofKind(e.target.value)}>
            <option value="">Choose…</option>
            {PROOF_KINDS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          Where is it kept? (a reference in your own records, never the document)
          <input value={proofRef} maxLength={64} onChange={(e) => setProofRef(e.target.value)} placeholder="file-1042" />
        </label>
        <fieldset style={{ border: "none", padding: 0, margin: "8px 0 0" }}>
          <legend><small>What may this person do? Choose only what is needed. Medical records stay closed.</small></legend>
          {SCOPES.map(([code, text]) => (
            <label key={code} style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={scopes.includes(code)}
                onChange={(e) => setScopes(e.target.checked ? [...scopes, code] : scopes.filter((s) => s !== code))} />
              {text}
            </label>
          ))}
        </fieldset>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          Allow until (optional)
          <input type="date" value={until} onChange={(e) => setUntil(e.target.value)}
            style={{ padding: "6px 8px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg)" }} />
        </label>
        {error && <small style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      <span>
        {r.status === "proposed" && <><button className="btn ghost" disabled={busy} onClick={() => void run(() => familyStep(r.id, "proof-needed"))}>Ask for proof</button>{" "}</>}
        {(r.status === "proposed" || r.status === "proof_required") && <><button className="btn ghost" disabled={busy} onClick={() => void run(() => familyStep(r.id, "proof-received"))}>Proof received</button>{" "}</>}
        <button className="btn" disabled={busy || !checked || scopes.length === 0 || !proofKind || proofRef.length < 4} onClick={() => void run(() => approveFamily(r.id, until, scopes, proofKind, proofRef))}>Approve</button>{" "}
        <button className="btn ghost" disabled={busy} onClick={() => void run(() => rejectFamily(r.id))}>Reject</button>
      </span>
    </div>
  );
}

function Approved({ r, onDone }: { r: FamilyRequest; onDone: () => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); setReason(""); await onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{r.name || "Unnamed"} <Pill kind={STATUS[r.status]?.kind ?? "info"}>{STATUS[r.status]?.text ?? r.status}</Pill></b>
        <small>Acts for them: {r.requested_by_name ?? r.requested_by}. {(r.scopes ?? []).join(", ")}</small>
        {r.proof_kind && <small>Proof seen: {PROOF_KINDS.find(([v]) => v === r.proof_kind)?.[1] ?? r.proof_kind}{r.proof_ref ? ` (${r.proof_ref})` : ""}.</small>}
        {error && <small style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      {r.status === "active" && (
        <span>
          <input aria-label={`Reason to pause ${r.name}`} placeholder="Reason to pause" value={reason} maxLength={200}
            onChange={(e) => setReason(e.target.value)} />{" "}
          <button className="btn ghost" disabled={busy || reason.trim().length < 3} onClick={() => void run(() => familyStep(r.id, "suspend", reason))}>Pause</button>
        </span>
      )}
      {r.status === "suspended" && <button className="btn" disabled={busy} onClick={() => void run(() => familyStep(r.id, "resume"))}>Resume</button>}
    </div>
  );
}

/** Guardians waiting for approval. The clinic checks documents at the desk, then ticks the box and approves. */
export function FamilyRequests({ staff }: { staff: Staff | null }) {
  const [rows, setRows] = useState<FamilyRequest[] | null>(null);
  const [approved, setApproved] = useState<FamilyRequest[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!staff) return;
    try { const d = await listFamilyRequests(); setRows(d.requests); setApproved(d.approved); setError(""); }
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
        <Card title={`${approved.length} approved link${approved.length === 1 ? "" : "s"}`}
          sub="Pause a link if there is a concern; nothing is shown to that person until you resume it.">
          {approved.length === 0 ? <div className="empty">No approved links yet.</div>
            : approved.map((r) => <Approved key={r.id} r={r} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
