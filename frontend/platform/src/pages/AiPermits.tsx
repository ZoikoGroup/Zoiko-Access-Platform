import { useCallback, useEffect, useState } from "react";
import { getCustomerAiPermits, getOverview, signCustomerAiPermit, type CustomerAiPermit } from "../api";
import type { TenantRow } from "../types";
import { Card, Pill } from "../ui";

// AI permits (decided 27 Nov 2026, DEV-399): a customer's AI feature runs only when one of its
// administrators AND a Zoiko officer have signed. Only staff reply drafts may use an outside model
// (Claude), with personal details removed first; nothing clinical can be authorised.

export function AiPermits() {
  const [customers, setCustomers] = useState<TenantRow[]>([]);
  const [customer, setCustomer] = useState("");
  const [rows, setRows] = useState<CustomerAiPermit[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getOverview().then((o) => setCustomers(o.tenants.filter((t) => t.type !== "platform"))).catch(() => setCustomers([]));
  }, []);
  const load = useCallback(async () => {
    if (!customer) { setRows(null); return; }
    try { setRows(await getCustomerAiPermits(customer)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, [customer]);
  useEffect(() => { void load(); }, [load]);

  async function sign(id: string) {
    setBusy(true); setError("");
    try { await signCustomerAiPermit(customer, id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="hero"><div>
        <h1>AI permits</h1>
        <p>An AI feature runs for a customer only after one of its administrators and a Zoiko officer have both signed its permit. Only staff reply drafts may use an outside model (Claude), with personal details removed first; nothing clinical can be authorised. Signing says you checked the purpose and the safeguards. It does not switch anything on: the customer authorises it afterwards.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title="Customer">
          <label>Choose a customer<select value={customer} onChange={(e) => setCustomer(e.target.value)}>
            <option value="">Choose…</option>
            {customers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select></label>
        </Card>
        {rows !== null && (
          <Card title={`${rows.length} permit${rows.length === 1 ? "" : "s"}`}>
            {rows.length === 0 && <div className="empty">This customer has no AI permit.</div>}
            {rows.map((p) => (
              <div key={p.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
                <div className="info">
                  <b>{p.feature_id} <Pill kind={p.state === "AUTHORIZED" ? "ok" : "info"}>{p.state.replace("_", " ").toLowerCase()}</Pill> {p.zoiko_signed && <Pill kind="ok">Zoiko signed</Pill>}</b>
                  <small>{p.purpose}. Lane {p.lane}, level {p.level}. Customer signatures: {p.approvers}.</small>
                </div>
                {!p.zoiko_signed && (p.state === "RELEASE_REVIEW" || p.state === "CHANGE_REVIEW") && (
                  <div className="form-row"><button className="btn" disabled={busy} onClick={() => void sign(p.id)}>Sign for Zoiko</button></div>
                )}
              </div>
            ))}
          </Card>
        )}
      </div>
    </>
  );
}
