import { useCallback, useEffect, useState } from "react";
import {
  ApiError, approveSuspension, cancelSuspension, getSuspensions, rejectSuspension, requestSuspension,
  type SuspensionRow,
} from "../api";
import type { TenantRow } from "../types";
import { Card, Pill } from "../ui";

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "You cannot do that: the person who asked cannot approve their own request, and only support staff can decide."
    : code === "CONFLICT_VERSION" ? "That no longer fits: a request is already waiting for this customer, it was already decided, or the customer is no longer in the right state. Refresh the page."
    : code === "NOT_FOUND" ? "That customer or request no longer exists. Refresh the page."
    : code === "VALIDATION_FAILED" ? "Check the text: a reason of up to 500 characters is needed (a note up to 300), with no line breaks in the reason."
    : e instanceof Error ? e.message : "That did not work";
}

const STATUS = {
  pending: { text: "Waiting for a second person", kind: "warn" },
  applied: { text: "Approved and applied", kind: "ok" },
  rejected: { text: "Rejected", kind: "bad" },
  cancelled: { text: "Taken back", kind: "info" },
} as const;

/** Suspending or reinstating a customer takes two different support people: one asks, another approves. */
export function Suspensions({ tenants, canWrite, onChanged }: {
  tenants: TenantRow[]; canWrite: boolean; onChanged: () => Promise<void>;
}) {
  const customers = tenants.filter((t) => t.type !== "platform");
  const [rows, setRows] = useState<SuspensionRow[] | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState<{ id: string; value: string } | null>(null);   // reason or note being typed

  const load = useCallback(async () => {
    try { setRows(await getSuspensions()); } catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setDone("");
    try { await work(); setDone(success); setText(null); await load(); await onChanged(); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }

  const waitingFor = (id: string) => rows?.find((r) => r.customer === id && r.status === "pending");
  const history = rows?.filter((r) => r.status !== "pending").slice(0, 10) ?? [];

  return (
    <Card title="Suspend or reinstate a customer"
      sub="Takes two different support people: one asks, with a reason, and another approves. Nothing changes until then. A suspended customer cannot sign in and its open sessions stop working at once. Every step is written to the audit log.">
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">{done}</div>}
      {customers.length === 0 ? <div className="empty">No customers yet.</div> : customers.map((t) => {
        const waiting = waitingFor(t.id);
        const suspended = t.status === "suspended";
        const open = text?.id === t.id;
        return (
          <div key={t.id} className="appt">
            <div className="info">
              <b>{t.name} <Pill kind={suspended ? "bad" : "ok"}>{suspended ? "Suspended" : t.status === "active" ? "Active" : t.status}</Pill></b>
              {waiting ? (
                <>
                  <span>{waiting.kind === "suspend" ? "Suspension" : "Reinstatement"} requested by {waiting.requested_by}: {waiting.reason}</span>
                  {canWrite && waiting.mine && (
                    <span><button className="btn ghost" disabled={busy} onClick={() => void run(() => cancelSuspension(waiting.id), "Request taken back.")}>Take my request back</button>
                      {" "}<span className="sub">Another support person has to decide it.</span></span>
                  )}
                  {canWrite && !waiting.mine && (open ? (
                    <span className="form-row">
                      <input aria-label="Note (optional)" placeholder="Note (optional)" maxLength={300}
                        value={text.value} onChange={(e) => setText({ id: t.id, value: e.target.value })} />
                      <button className="btn" disabled={busy}
                        onClick={() => void run(() => approveSuspension(waiting.id), waiting.kind === "suspend" ? "Customer suspended." : "Customer reinstated.")}>
                        Approve and apply
                      </button>
                      <button className="btn ghost" disabled={busy}
                        onClick={() => void run(() => rejectSuspension(waiting.id, text.value), "Request rejected.")}>Reject</button>
                      <button className="btn ghost" onClick={() => setText(null)}>Cancel</button>
                    </span>
                  ) : <span><button className="btn" onClick={() => setText({ id: t.id, value: "" })}>Review this request</button></span>)}
                </>
              ) : canWrite && (open ? (
                <span className="form-row">
                  <input aria-label="Reason" placeholder="Reason (no patient details)" maxLength={500}
                    value={text.value} onChange={(e) => setText({ id: t.id, value: e.target.value })} />
                  <button className="btn" disabled={busy || !text.value.trim()}
                    onClick={() => void run(() => requestSuspension(t.id, suspended ? "reinstate" : "suspend", text.value), "Request sent. A second person must approve it.")}>
                    Send request
                  </button>
                  <button className="btn ghost" onClick={() => setText(null)}>Cancel</button>
                </span>
              ) : (
                <span><button className="btn ghost" onClick={() => { setText({ id: t.id, value: "" }); setError(""); setDone(""); }}>
                  {suspended ? "Ask to reinstate" : "Ask to suspend"}
                </button></span>
              ))}
            </div>
          </div>
        );
      })}
      {history.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>Recent decisions</h3>
          {history.map((r) => (
            <div key={r.id} className="appt" style={{ padding: "6px 0" }}>
              <div className="info">
                <b>{r.customer_name} <Pill kind={STATUS[r.status].kind}>{STATUS[r.status].text}</Pill></b>
                <span className="sub">{r.kind === "suspend" ? "Suspension" : "Reinstatement"} asked by {r.requested_by}: {r.reason}
                  {r.decided_by ? ` - ${r.status === "cancelled" ? "taken back" : "decided"} by ${r.decided_by}` : ""}
                  {r.decision_note ? ` (${r.decision_note})` : ""}</span>
              </div>
            </div>
          ))}
        </>
      )}
      {!canWrite && <div className="notice">Your role can look at these requests but not make or decide them.</div>}
    </Card>
  );
}
