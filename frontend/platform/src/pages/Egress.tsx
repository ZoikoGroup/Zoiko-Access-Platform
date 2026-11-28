import { useCallback, useEffect, useState } from "react";
import { decideEgress, getEgress, proposeEgress, type EgressData, type EgressPermit } from "../api";
import { Card, Pill } from "../ui";

// Outgoing addresses (DEV-384): a clinic's data leaves the cell only for a host Zoiko approved for
// that clinic. Clinics ask by registering a webhook address; Zoiko staff approve, refuse or revoke.

const STATE: Record<EgressPermit["state"], { text: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  proposed: { text: "Waiting for approval", kind: "warn" },
  approved: { text: "Approved", kind: "ok" },
  refused: { text: "Refused", kind: "bad" },
  revoked: { text: "Revoked", kind: "info" },
};
const MODE: Record<string, string> = {
  enforce: "Enforced: nothing goes to an address that is not approved.",
  report: "Report only (development): sends anyway and logs what would be refused.",
  off: "Off: not checked.",
};

export function Egress() {
  const [data, setData] = useState<EgressData | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState<Record<string, string>>({});
  const [form, setForm] = useState({ customer: "", host: "", purpose: "connector" });

  const load = useCallback(async () => {
    try { setData(await getEgress()); } catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<unknown>, message: string) {
    setBusy(true); setError(""); setDone("");
    try { await work(); setDone(message); await load(); }
    catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "POLICY_DENIED" ? "You cannot approve an address you proposed yourself: ask a colleague."
        : code === "VALIDATION_FAILED" ? "Give an exact host name like lab.example.com (no wildcards, no IP numbers), and a reason to refuse or revoke."
        : code === "CONFLICT_VERSION" ? "It has already been decided. Reload."
        : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="hero"><div>
        <h1>Outgoing addresses</h1>
        <p>A clinic's webhooks, and later its partner connections, go only to a host Zoiko has approved for that clinic.
          Clinics ask by registering an address. Nobody approves an address they proposed themselves.</p>
        {data && <p><Pill kind={data.mode === "enforce" ? "ok" : "warn"}>{data.mode}</Pill> {MODE[data.mode] ?? ""}</p>}
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">{done}</div>}
      <Card title="Addresses" sub="Waiting ones first.">
        {!data ? <p>Loading…</p> : data.permits.length === 0 ? <p>No address has been asked for yet.</p> : data.permits.map((x) => (
          <div key={x.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <div className="info">
              <b><code>{x.host}</code> · {x.customer_name || x.customer} <Pill kind={STATE[x.state].kind}>{STATE[x.state].text}</Pill></b>
              <small>{x.purpose === "webhook" ? "The clinic's own webhook receiver" : "A partner system"} · asked by {x.proposed_by} on {x.asked_at.slice(0, 10)}
                {x.decided_by ? ` · ${x.state} by ${x.decided_by}${x.reason ? `: ${x.reason}` : ""}` : ""}</small>
            </div>
            {data.can_decide && (x.state === "proposed" || x.state === "approved") && (
              <div className="form-row">
                <input aria-label={`Reason for ${x.host}`} placeholder="Reason (needed to refuse or revoke)" maxLength={300}
                  value={reason[x.id] ?? ""} onChange={(e) => setReason({ ...reason, [x.id]: e.target.value })} />
                {x.state === "proposed" && <>
                  <button className="btn" disabled={busy} onClick={() => void run(() => decideEgress(x.id, "approve", ""), `${x.host} approved.`)}>Approve</button>
                  <button className="btn ghost" disabled={busy || (reason[x.id] ?? "").trim().length < 3}
                    onClick={() => void run(() => decideEgress(x.id, "refuse", reason[x.id] ?? ""), `${x.host} refused.`)}>Refuse</button>
                </>}
                {x.state === "approved" && (
                  <button className="btn ghost" disabled={busy || (reason[x.id] ?? "").trim().length < 3}
                    onClick={() => void run(() => decideEgress(x.id, "revoke", reason[x.id] ?? ""), `${x.host} revoked: nothing more is sent there.`)}>Revoke</button>
                )}
              </div>
            )}
          </div>
        ))}
      </Card>
      {data?.can_decide && (
        <Card title="Propose an address" sub="For a partner system a connector will call. A colleague must approve it.">
          <div className="form-row">
            <select aria-label="Customer" value={form.customer} onChange={(e) => setForm({ ...form, customer: e.target.value })}>
              <option value="">Customer…</option>
              {data.customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <input aria-label="Host name" placeholder="lab.example.com" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} />
            <select aria-label="Purpose" value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })}>
              <option value="connector">Partner system</option>
              <option value="webhook">Clinic webhook receiver</option>
            </select>
            <button className="btn" disabled={busy || !form.customer || !form.host.trim()}
              onClick={() => void run(async () => { await proposeEgress(form.customer, form.host.trim(), form.purpose); setForm({ ...form, host: "" }); }, "Proposed. A colleague must approve it.")}>Propose</button>
          </div>
        </Card>
      )}
    </>
  );
}
