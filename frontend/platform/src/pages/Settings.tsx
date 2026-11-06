import { useState } from "react";
import { ApiError, setCustomerSignUp } from "../api";
import type { TenantRow } from "../types";
import { Card, Pill } from "../ui";
import { Suspensions } from "./Suspensions";

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "Your account may not change customer settings."
    : code === "NOT_FOUND" ? "That customer no longer exists. Refresh the page."
    : code === "VALIDATION_FAILED" ? "That setting cannot be changed for this customer."
    : e instanceof Error ? e.message : "That did not work";
}

/** Platform settings. Today there is one: whether patients may create their own account at a customer. */
export function Settings({ tenants, canWrite, onChanged }: {
  tenants: TenantRow[]; canWrite: boolean; onChanged: () => Promise<void>;
}) {
  const customers = tenants.filter((t) => t.type !== "platform");
  const [asking, setAsking] = useState("");        // the customer whose change is waiting for "Yes"
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function change(t: TenantRow, enabled: boolean) {
    setBusy(true); setError(""); setDone("");
    try {
      await setCustomerSignUp(t.id, enabled);
      setDone(`${t.name}: patient sign-up is now ${enabled ? "open" : "closed"}.`);
      setAsking("");
      await onChanged();
    } catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="hero">
        <div>
          <h1>Settings</h1>
          <p>Settings for each customer: patient sign-up, and suspending or reinstating a customer (which takes two different support people).</p>
        </div>
      </div>
      <div className="grid">
        <Card title="Patient sign-up" sub="When open, anyone can create a patient account at that customer from its own sign-in page. New customers start closed. Every change is recorded in the audit log of both the platform and the customer.">
          {error && <div className="error" role="alert">{error}</div>}
          {done && <div className="success" role="status">{done}</div>}
          {customers.length === 0 ? <div className="empty">No customers yet.</div> : customers.map((t) => (
            <div key={t.id} className="appt">
              <div className="info">
                <b>{t.name} <Pill kind={t.self_registration ? "warn" : "ok"}>{t.self_registration ? "Open" : "Closed"}</Pill></b>
                <span className="sub">{t.type.replace("_", " ")} - {t.status}</span>
                {canWrite && (asking === t.id ? (
                  <span className="form-row">
                    <span>{t.self_registration ? "Close sign-up so only invited people can join?" : "Let anyone create a patient account here?"}</span>
                    <button className="btn" disabled={busy} onClick={() => void change(t, !t.self_registration)}>Yes, {t.self_registration ? "close it" : "open it"}</button>
                    <button className="btn ghost" disabled={busy} onClick={() => setAsking("")}>Cancel</button>
                  </span>
                ) : (
                  <span>
                    <button className="btn ghost" disabled={busy} onClick={() => { setAsking(t.id); setError(""); setDone(""); }}>
                      {t.self_registration ? "Close sign-up" : "Open sign-up"}
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
          {!canWrite && <div className="notice">Your role can look at these settings but not change them.</div>}
        </Card>
        <Suspensions tenants={tenants} canWrite={canWrite} onChanged={onChanged} />
      </div>
    </>
  );
}
