import { useState } from "react";
import { ApiError, addCustomer } from "../api";
import { Card } from "../ui";

/** Support adds a customer. The administrator is only told by email: nothing about them is shown here. */
export function AddCustomer({ onAdded }: { onAdded: () => Promise<void> }) {
  const [customer, setCustomer] = useState("");
  const [site, setSite] = useState("");
  const [email, setEmail] = useState("");
  const [kind, setKind] = useState("provider");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setDone("");
    try {
      const made = await addCustomer({ customer, site, admin_email: email, kind });
      setDone(`${made.customer.name} was added. An invitation was emailed to its first user, who chooses their own password.`);
      setCustomer(""); setSite(""); setEmail("");
      await onAdded();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "";
      setError(code === "CONFLICT_VERSION" ? "A customer with that name already exists."
        : code === "VALIDATION_FAILED" ? "Check the three fields: names need 2 to 120 characters and the email must be a real address."
        : code === "POLICY_DENIED" ? "Your account may not add customers."
        : err instanceof Error ? err.message : "Could not add the customer");
    } finally { setBusy(false); }
  }

  return (
    <Card title="Add a customer" sub="Creates the organization, its first site and its first administrator, then emails the administrator an invitation.">
      <form className="add-form" onSubmit={(e) => void submit(e)}>
        <label>Kind of customer
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="provider">Clinic or hospital (administrator)</option>
            <option value="lab">Laboratory or imaging partner</option>
            <option value="pharmacy">Pharmacy partner</option>
            <option value="homecare">Home-care partner</option>
            <option value="payer">Payer or insurer</option>
            <option value="employer">Employer sponsor</option>
            <option value="developer">Integration partner developer</option>
          </select>
        </label>
        <label>Customer name (what they type at sign-in)
          <input value={customer} maxLength={120} onChange={(e) => setCustomer(e.target.value)} required />
        </label>
        <label>First site (clinic or hospital)
          <input value={site} maxLength={120} onChange={(e) => setSite(e.target.value)} required />
        </label>
        <label>First user's email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />
        </label>
        {error && <div className="error" role="alert">{error}</div>}
        {done && <div className="success" role="status">{done}</div>}
        <div><button className="btn" type="submit" disabled={busy || !customer.trim() || !site.trim() || !email.trim()}>
          {busy ? "Adding…" : "Add customer"}
        </button></div>
      </form>
    </Card>
  );
}
