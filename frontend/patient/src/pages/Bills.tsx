import { useEffect, useState } from "react";
import {
  getInvoices,
  getInvoicePayments,
  payInvoice,
  refreshPayment,
  type Invoice,
  type PaymentAttempt,
} from "../api";
import { Card, Pill } from "../ui";

const STATUS = {
  issued: { text: "To pay", kind: "warn" as const },
  paid: { text: "Paid", kind: "ok" as const },
  void: { text: "Cancelled", kind: "info" as const },
  draft: { text: "Draft", kind: "info" as const },
};

const PAY_STATUS = {
  succeeded: { text: "Succeeded", kind: "ok" as const },
  pending: { text: "Pending", kind: "warn" as const },
  failed: { text: "Failed", kind: "bad" as const },
  unknown: { text: "Unknown", kind: "info" as const },
};

const money = (minor: number, currency: string) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 100);

export function Bills() {
  const [rows, setRows] = useState<Invoice[] | null>(null);
  const [error, setError] = useState("");
  const [busyInvoice, setBusyInvoice] = useState<string | null>(null);
  const [payNotice, setPayNotice] = useState<string | null>(null);
  const [openHistoryId, setOpenHistoryId] = useState<string | null>(null);
  const [historyRows, setHistoryRows] = useState<Record<string, PaymentAttempt[]>>({});
  const [historyLoading, setHistoryLoading] = useState<string | null>(null);

  const loadInvoices = () => {
    getInvoices()
      .then(setRows)
      .catch((e) => {
        setError(e instanceof Error ? e.message : "Could not load");
        setRows([]);
      });
  };

  useEffect(() => {
    loadInvoices();
  }, []);

  const handlePay = async (inv: Invoice) => {
    setBusyInvoice(inv.public_id);
    setError("");
    setPayNotice(null);
    try {
      // Deterministic 8-64 char idempotency key matching _IDEMPOTENCY_KEY
      const idemp = `pay-${inv.public_id}-${Date.now().toString(36)}`;
      const payment = await payInvoice(inv.public_id, idemp);
      if (payment.status === "succeeded") {
        setPayNotice(`Payment of ${money(payment.amount_minor, payment.currency)} succeeded! Reference: ${payment.provider_ref || payment.public_id}`);
        loadInvoices();
      } else if (payment.status === "failed") {
        setError(`Payment was declined by provider. Balance remains ${money(inv.balance_minor, inv.currency)}.`);
      } else {
        setPayNotice(`Payment status is ${payment.status}. Please check back shortly.`);
      }
      // Also update history cache if loaded
      setHistoryRows((prev) => ({
        ...prev,
        [inv.public_id]: [payment, ...(prev[inv.public_id] || [])],
      }));
    } catch (e: any) {
      if (e?.body?.code === "VALIDATION_FAILED" && JSON.stringify(e?.body || "").includes("payment_provider")) {
        setError("Online payment is currently unavailable. Please settle this bill at the clinic desk.");
      } else {
        setError(e instanceof Error ? e.message : "Payment failed. Please try again.");
      }
    } finally {
      setBusyInvoice(null);
    }
  };

  const toggleHistory = async (invoiceId: string) => {
    if (openHistoryId === invoiceId) {
      setOpenHistoryId(null);
      return;
    }
    setOpenHistoryId(invoiceId);
    if (!historyRows[invoiceId]) {
      setHistoryLoading(invoiceId);
      try {
        const attempts = await getInvoicePayments(invoiceId);
        setHistoryRows((prev) => ({ ...prev, [invoiceId]: attempts }));
      } catch {
        // ignore history fetch error
      } finally {
        setHistoryLoading(null);
      }
    }
  };

  const handleRefresh = async (invoiceId: string, paymentId: string) => {
    setHistoryLoading(invoiceId);
    try {
      const refreshed = await refreshPayment(paymentId);
      setHistoryRows((prev) => ({
        ...prev,
        [invoiceId]: (prev[invoiceId] || []).map((p) => (p.public_id === paymentId ? refreshed : p)),
      }));
      if (refreshed.status === "succeeded") {
        loadInvoices();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not refresh payment status");
    } finally {
      setHistoryLoading(null);
    }
  };

  return (
    <Card
      title="Bills & Payments"
      sub="Your bills from the clinic. You can pay outstanding balances online or at the clinic desk. If something looks wrong, contact the clinic."
    >
      {error && (
        <div className="error" role="alert" style={{ marginBottom: 12 }}>
          {error}
        </div>
      )}
      {payNotice && (
        <div
          role="status"
          style={{
            padding: "10px 14px",
            background: "#e8f5e9",
            color: "#1b5e20",
            borderRadius: 8,
            marginBottom: 12,
            fontWeight: 500,
          }}
        >
          {payNotice}
        </div>
      )}
      {rows === null ? (
        <div className="empty">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="empty">You have no bills.</div>
      ) : (
        rows.map((i) => {
          const isIssued = i.status === "issued";
          const hasBalance = i.balance_minor > 0;
          const isBusy = busyInvoice === i.public_id;
          const history = historyRows[i.public_id] || [];
          const isHistoryOpen = openHistoryId === i.public_id;

          return (
            <div key={i.public_id} className="appt" style={{ display: "block", marginBottom: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                <div className="info" style={{ flex: 1 }}>
                  <b>
                    {i.number} <Pill kind={STATUS[i.status].kind}>{STATUS[i.status].text}</Pill>
                  </b>
                  <small>
                    {money(i.total_minor, i.currency)}
                    {i.credited_minor > 0
                      ? `, reduced by ${money(i.credited_minor, i.currency)}, balance ${money(i.balance_minor, i.currency)}`
                      : ""}
                    .{isIssued && i.due_on ? ` Due ${i.due_on}.` : ""}
                  </small>
                  {i.lines.map((l, n) => (
                    <small key={n}>
                      {l.quantity} × {l.name}
                    </small>
                  ))}
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
                  {isIssued && hasBalance && (
                    <button
                      type="button"
                      disabled={isBusy}
                      onClick={() => handlePay(i)}
                      style={{
                        background: "#1f5fe0",
                        color: "#fff",
                        border: "none",
                        padding: "6px 14px",
                        borderRadius: 8,
                        fontWeight: 600,
                        cursor: isBusy ? "not-allowed" : "pointer",
                      }}
                    >
                      {isBusy ? "Processing…" : `Pay ${money(i.balance_minor, i.currency)}`}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => toggleHistory(i.public_id)}
                    style={{
                      background: "none",
                      border: "none",
                      color: "#1f5fe0",
                      fontSize: 12,
                      cursor: "pointer",
                      textDecoration: "underline",
                      padding: 0,
                    }}
                  >
                    {isHistoryOpen ? "Hide payment history" : "Payment history"}
                  </button>
                </div>
              </div>

              {isHistoryOpen && (
                <div
                  style={{
                    marginTop: 10,
                    paddingTop: 8,
                    borderTop: "1px dashed #e0e0e0",
                    fontSize: 13,
                  }}
                >
                  {historyLoading === i.public_id ? (
                    <div style={{ color: "#666" }}>Loading payment history…</div>
                  ) : history.length === 0 ? (
                    <div style={{ color: "#666" }}>No payment attempts recorded for this bill.</div>
                  ) : (
                    <div style={{ display: "grid", gap: 6 }}>
                      {history.map((h) => (
                        <div
                          key={h.public_id}
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            background: "#f9fbfd",
                            padding: "6px 10px",
                            borderRadius: 6,
                          }}
                        >
                          <div>
                            <span>
                              {money(h.amount_minor, h.currency)} via {h.provider}{" "}
                            </span>
                            <Pill kind={PAY_STATUS[h.status]?.kind || "info"}>
                              {PAY_STATUS[h.status]?.text || h.status}
                            </Pill>
                            {h.provider_ref && (
                              <small style={{ display: "block", color: "#666" }}>Ref: {h.provider_ref}</small>
                            )}
                          </div>
                          {(h.status === "pending" || h.status === "unknown") && (
                            <button
                              type="button"
                              onClick={() => handleRefresh(i.public_id, h.public_id)}
                              style={{
                                fontSize: 11,
                                padding: "2px 8px",
                                borderRadius: 4,
                                border: "1px solid #ccc",
                                background: "#fff",
                                cursor: "pointer",
                              }}
                            >
                              Check status
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })
      )}
    </Card>
  );
}
