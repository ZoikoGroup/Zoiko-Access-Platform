import { useCallback, useEffect, useState } from "react";
import { billingAction, createInvoice, getBillingFees, getInvoice, getInvoices, getPatients, setFee } from "../api";
import type { FeeRow, InvoiceRow, PatientRow } from "../types";
import { Card, Pill, type PillKind } from "../ui";

const STATUS: Record<InvoiceRow["status"], { text: string; kind: PillKind }> = {
  draft: { text: "Draft", kind: "info" }, issued: { text: "Issued", kind: "warn" },
  paid: { text: "Marked as paid", kind: "ok" }, void: { text: "Cancelled", kind: "bad" },
};
const REASONS = [["error", "A mistake"], ["service_not_given", "Service not given"], ["duplicate", "A duplicate"], ["goodwill", "Goodwill"], ["other", "Other"]];

const money = (minor: number, currency: string) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 100);

function Fees({ canSet }: { canSet: boolean }) {
  const [rows, setRows] = useState<FeeRow[] | null>(null);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [currency, setCurrency] = useState("GBP");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setRows((await getBillingFees()).fees); } catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function save(ref: string) {
    setError("");
    const major = Number(amounts[ref]);
    if (!Number.isFinite(major) || major < 0) { setError("Enter an amount such as 45.00."); return; }
    try { await setFee(ref, currency, Math.round(major * 100)); setAmounts({ ...amounts, [ref]: "" }); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
  }
  return (
    <Card title="Prices" sub="What each service costs. An invoice takes the price in force when it is made; changing a price never changes an invoice already made. Only a clinic administrator sets prices.">
      {error && <div className="error" role="alert">{error}</div>}
      {canSet && <label>Currency (3 letters)<input value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} style={{ width: 80 }} /></label>}
      {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">No services yet.</div> : rows.map((r) => (
        <div key={r.service_ref} className="appt"><div className="info">
          <b>{r.name}</b>
          <small>{r.current ? `${money(r.current.amount_minor, r.current.currency)} from ${r.current.effective_from}.` : "No price set: it cannot be invoiced."}{r.upcoming ? ` Changes to ${money(r.upcoming.amount_minor, r.upcoming.currency)} on ${r.upcoming.effective_from}.` : ""}</small>
          {canSet && (
            <span className="form-row">
              <input aria-label={`New price for ${r.name}`} inputMode="decimal" placeholder="45.00" value={amounts[r.service_ref] ?? ""} onChange={(e) => setAmounts({ ...amounts, [r.service_ref]: e.target.value })} style={{ width: 110 }} />
              <button className="btn ghost" disabled={!amounts[r.service_ref]} onClick={() => void save(r.service_ref)}>Set price</button>
            </span>
          )}
        </div></div>
      ))}
    </Card>
  );
}

function NewInvoice({ fees, onDone }: { fees: FeeRow[]; onDone: () => Promise<void> }) {
  const [patients, setPatients] = useState<PatientRow[]>([]);
  const [person, setPerson] = useState("");
  const [service, setService] = useState("");
  const [qty, setQty] = useState("1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { getPatients().then(setPatients).catch(() => setPatients([])); }, []);
  const priced = fees.filter((f) => f.current);
  async function make(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await createInvoice(person, [{ service, quantity: Number(qty) }]); await onDone(); }
    catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Card title="New invoice" sub="Priced from the price list. It starts as a draft. Only patients of this clinic can be billed.">
      <form className="profile-form" onSubmit={(e) => void make(e)}>
        <label>Patient<select value={person} onChange={(e) => setPerson(e.target.value)} required>
          <option value="">Choose…</option>{patients.map((p) => <option key={p.id} value={p.id}>{p.patient_name ?? p.id}</option>)}
        </select></label>
        <label>Service<select value={service} onChange={(e) => setService(e.target.value)} required>
          <option value="">Choose…</option>{priced.map((f) => <option key={f.service_ref} value={f.service_ref}>{f.name}</option>)}
        </select></label>
        <label>Quantity<input type="number" min={1} max={20} value={qty} onChange={(e) => setQty(e.target.value)} /></label>
        {error && <div className="error" role="alert">{error}</div>}
        <div><button className="btn" type="submit" disabled={busy || !person || !service}>Make the invoice</button></div>
      </form>
    </Card>
  );
}

function Row({ inv, canAdmin, onDone }: { inv: InvoiceRow; canAdmin: boolean; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<InvoiceRow | null>(null);
  const [ref, setRef] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("error");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function run(action: "issue" | "pay" | "void" | "credit") {
    setBusy(true); setError("");
    try {
      await billingAction(inv.public_id, action, action === "pay" ? { payment_ref: ref }
        : action === "void" ? { reason } : action === "credit" ? { amount_minor: Math.round(Number(amount) * 100), reason } : {});
      setRef(""); setAmount(""); setDetail(null); await onDone();
    } catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  async function toggle() {
    setOpen(!open);
    if (!open && !detail) { try { setDetail(await getInvoice(inv.public_id)); } catch { /* shown below */ } }
  }
  const s = STATUS[inv.status];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{inv.number} <Pill kind={s.kind}>{s.text}</Pill> {inv.patient_name ?? ""}</b>
        <small>{money(inv.total_minor, inv.currency)}{inv.credited_minor > 0 ? `, credited ${money(inv.credited_minor, inv.currency)}, balance ${money(inv.balance_minor, inv.currency)}` : ""}. Due {inv.due_on ?? "n/a"}.{inv.payment_ref ? ` Payment ref ${inv.payment_ref}.` : ""}</small>
        {error && <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      <div className="form-row">
        {inv.status === "draft" && <button className="btn" disabled={busy} onClick={() => void run("issue")}>Issue</button>}
        {inv.status === "issued" && (
          <>
            <input aria-label={`Payment reference for ${inv.number}`} placeholder="Payment reference" value={ref} maxLength={64} onChange={(e) => setRef(e.target.value)} />
            <button className="btn" disabled={busy || ref.length < 4} onClick={() => void run("pay")}>Mark as paid</button>
          </>
        )}
        {canAdmin && inv.status !== "void" && (
          <select aria-label={`Reason for ${inv.number}`} value={reason} onChange={(e) => setReason(e.target.value)}>
            {REASONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        )}
        {canAdmin && inv.status !== "void" && inv.credited_minor === 0 && <button className="btn ghost" disabled={busy} onClick={() => void run("void")}>Cancel invoice</button>}
        {canAdmin && (inv.status === "issued" || inv.status === "paid") && (
          <>
            <input aria-label={`Credit amount for ${inv.number}`} inputMode="decimal" placeholder="Credit, e.g. 10.00" value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: 130 }} />
            <button className="btn ghost" disabled={busy || !amount} onClick={() => void run("credit")}>Credit note</button>
          </>
        )}
        <button className="btn ghost" aria-expanded={open} onClick={() => void toggle()}>{open ? "Hide the history" : "Show the history"}</button>
      </div>
      {open && detail && (
        <div className="info">
          <small>{detail.chain_ok ? "The history checks out." : "The history does NOT check out."}</small>
          {detail.lines.map((l, i) => <small key={i}>{l.quantity} x {l.name} at {money(l.unit_minor, inv.currency)}</small>)}
          {detail.credit_notes.map((c) => <small key={c.number}>{c.number}: {money(c.amount_minor, inv.currency)} ({c.reason})</small>)}
          {detail.history?.map((h) => <small key={h.seq}>{h.seq}. {h.kind} by {h.actor}, {h.at.slice(0, 16).replace("T", " ")}</small>)}
        </div>
      )}
    </div>
  );
}

/** A clinic's fees and invoices. Records only: no money moves here and no card detail is held. */
export function Billing() {
  const [fees, setFees] = useState<FeeRow[]>([]);
  const [canSet, setCanSet] = useState(false);
  const [invoices, setInvoices] = useState<InvoiceRow[] | null>(null);
  const [canAdmin, setCanAdmin] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const f = await getBillingFees(); setFees(f.fees); setCanSet(f.can_set);
      const i = await getInvoices(); setInvoices(i.invoices); setCanAdmin(i.can_admin); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load billing"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <>
      <div className="hero"><div>
        <h1>Billing</h1>
        <p>Prices, invoices and credit notes for this clinic. This keeps the records: it does not take payments and holds no card or bank details. When you mark an invoice as paid, you give the reference of a payment you took elsewhere. Credit notes can never take an invoice below zero. There is no tax handling yet, and a patient sees only their own issued invoices.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Fees canSet={canSet} />
        <NewInvoice fees={fees} onDone={load} />
        <Card title={`Invoices (${invoices?.length ?? 0})`}>
          {invoices === null ? <div className="empty">Loading…</div> : invoices.length === 0
            ? <div className="empty">No invoice yet.</div> : invoices.map((i) => <Row key={i.public_id} inv={i} canAdmin={canAdmin} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
