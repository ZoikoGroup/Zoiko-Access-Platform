import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getBills, getPayments, payBill, refreshPayment, type Invoice, type OnlinePayment, type Payment } from "../api";
import { Card, Pill } from "../ui";

const STATUS = {
  issued: { text: "To pay", kind: "warn" as const }, paid: { text: "Paid", kind: "ok" as const },
  void: { text: "Cancelled", kind: "info" as const }, draft: { text: "Draft", kind: "info" as const },
};
const OUTCOME: Record<Payment["status"], { text: string; kind: "ok" | "bad" | "warn" }> = {
  succeeded: { text: "Payment received", kind: "ok" },
  failed: { text: "Payment did not go through: nothing was taken", kind: "bad" },
  pending: { text: "Still being processed", kind: "warn" },
  unknown: { text: "We do not know yet whether it went through", kind: "warn" },
};
const money = (minor: number, currency: string) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 100);
const newKey = () => crypto.randomUUID();

/** One bill, with paying it online where the clinic's system has that switched on. */
function Bill({ i, online, onChanged }: { i: Invoice; online: OnlinePayment; onChanged: () => Promise<void> }) {
  const [tries, setTries] = useState<Payment[] | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useRef("");                     // the same key for a retry, so a bill is never paid twice

  const show = useCallback(async () => {
    try { setTries(await getPayments(i.public_id)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the payments"); }
  }, [i.public_id]);

  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await show(); await onChanged(); }
    catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? "This bill changed while you were paying. Look again before trying once more."
        : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); setAsking(false); }
  }
  const pay = () => run(async () => {
    key.current ||= newKey();
    await payBill(i.public_id, key.current);
    key.current = "";                         // answered: a new attempt would be a new payment
  });
  const canPay = online.available && i.status === "issued" && i.balance_minor > 0;

  return (
    <div className="appt">
      <div className="info">
        <b>{i.number} <Pill kind={STATUS[i.status].kind}>{STATUS[i.status].text}</Pill></b>
        <small>{money(i.total_minor, i.currency)}{i.credited_minor > 0 ? `, reduced by ${money(i.credited_minor, i.currency)}, now ${money(i.balance_minor, i.currency)}` : ""}.{i.status === "issued" && i.due_on ? ` Due ${i.due_on}.` : ""}</small>
        {i.lines.map((l, n) => <small key={n}>{l.quantity} x {l.name}</small>)}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      <div className="row">
        {canPay && !asking && <button className="btn" onClick={() => setAsking(true)}>Pay {money(i.balance_minor, i.currency)}</button>}
        {canPay && asking && (
          <>
            <button className="btn" disabled={busy} onClick={() => void pay()}>{busy ? "Paying…" : `Yes, pay ${money(i.balance_minor, i.currency)}`}</button>
            <button className="btn ghost" disabled={busy} onClick={() => setAsking(false)}>Not now</button>
          </>
        )}
        {i.status !== "void" && (
          <button className="btn ghost" aria-expanded={tries !== null} onClick={() => (tries === null ? void show() : setTries(null))}>
            {tries === null ? "Payments" : "Hide payments"}<span className="sr-only"> for {i.number}</span>
          </button>
        )}
      </div>
      {tries !== null && (
        <div style={{ flexBasis: "100%" }} role="status">
          {tries.length === 0 ? <small>No online payment has been tried for this bill.</small> : tries.map((t) => (
            <div key={t.public_id} className="row">
              <Pill kind={OUTCOME[t.status].kind}>{OUTCOME[t.status].text}</Pill>
              <small>{money(t.amount_minor, t.currency)} · {new Date(t.created_at).toLocaleString()}{t.provider_ref ? ` · reference ${t.provider_ref}` : ""}</small>
              {(t.status === "pending" || t.status === "unknown") && (
                <button className="btn ghost" disabled={busy} onClick={() => void run(() => refreshPayment(t.public_id))}>Check again</button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The patient's own bills, and paying them where online payment is switched on. */
export function Bills() {
  const [data, setData] = useState<{ invoices: Invoice[]; online: OnlinePayment } | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await getBills()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <>
      <div className="hero"><div>
        <h1>Bills &amp; Payments</h1>
        <p>Your bills from the clinic. {data?.online.available
          ? "You can pay a bill here. Card details are never given to Zoiko Access."
          : "You cannot pay here: the clinic takes payment and marks the bill as paid."} If something looks wrong, contact the clinic.</p>
      </div></div>
      {data?.online.pretend && <div className="notice" role="status">Practice payments only: no real money moves on this system.</div>}
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Bills (${data?.invoices.length ?? 0})`}>
          {data === null ? <div className="empty">{error ? "Could not load your bills." : "Loading…"}</div>
            : data.invoices.length === 0 ? <div className="empty">You have no bills.</div>
            : data.invoices.map((i) => <Bill key={i.public_id} i={i} online={data.online} onChanged={load} />)}
        </Card>
      </div>
    </>
  );
}
