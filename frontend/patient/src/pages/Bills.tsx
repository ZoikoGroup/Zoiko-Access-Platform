import { useEffect, useState } from "react";
import { getInvoices, type Invoice } from "../api";
import { Card, Pill } from "../ui";

const STATUS = {
  issued: { text: "To pay", kind: "warn" as const }, paid: { text: "Paid", kind: "ok" as const },
  void: { text: "Cancelled", kind: "info" as const }, draft: { text: "Draft", kind: "info" as const },
};
const money = (minor: number, currency: string) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 100);

/** The patient's own bills. Nothing can be paid here: the clinic takes payment and records it. */
export function Bills() {
  const [rows, setRows] = useState<Invoice[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { getInvoices().then(setRows).catch((e) => { setError(e instanceof Error ? e.message : "Could not load"); setRows([]); }); }, []);
  return (
    <Card title="Bills" sub="Your bills from the clinic. You cannot pay here: the clinic takes payment and marks the bill as paid. If something looks wrong, contact the clinic.">
      {error && <div className="error" role="alert">{error}</div>}
      {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">You have no bills.</div> : rows.map((i) => (
        <div key={i.public_id} className="appt"><div className="info">
          <b>{i.number} <Pill kind={STATUS[i.status].kind}>{STATUS[i.status].text}</Pill></b>
          <small>{money(i.total_minor, i.currency)}{i.credited_minor > 0 ? `, reduced by ${money(i.credited_minor, i.currency)}, now ${money(i.balance_minor, i.currency)}` : ""}.{i.status === "issued" && i.due_on ? ` Due ${i.due_on}.` : ""}</small>
          {i.lines.map((l, n) => <small key={n}>{l.quantity} x {l.name}</small>)}
        </div></div>
      ))}
    </Card>
  );
}
