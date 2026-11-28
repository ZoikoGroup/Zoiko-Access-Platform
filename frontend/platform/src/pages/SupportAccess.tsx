import { useCallback, useEffect, useState } from "react";
import { askSupportAccess, endSupportAccess, getSupportAccess, lookSupportAccess, type SupportAccessData, type SupportAccessRow } from "../api";
import { Card, Pill } from "../ui";

// Short-term support access (DEV-381): Zoiko support asks a customer for a few minutes of access to
// setup and status views (never patients). The customer's administrator approves it; every look is
// recorded on the customer's own audit log.

const STATE: Record<SupportAccessRow["state"], { text: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  requested: { text: "Waiting for the customer", kind: "info" },
  lapsed: { text: "Not answered in time", kind: "warn" },
  active: { text: "Active", kind: "ok" },
  expired: { text: "Time is up", kind: "info" },
  refused: { text: "Refused by the customer", kind: "bad" },
  ended: { text: "Ended", kind: "info" },
};

function minutesLeft(row: SupportAccessRow, now: number): string {
  if (row.state !== "active" || !row.ends_at) return "";
  const left = Math.max(0, Math.ceil((Date.parse(row.ends_at) - now) / 60000));
  return `${left} minute${left === 1 ? "" : "s"} left`;
}

/** One view, shown plainly: tables of simple rows, and anything else as numbers. */
function View({ data }: { data: Record<string, unknown> }) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      {Object.entries(data).map(([key, value]) => {
        const title = key.replace(/_/g, " ");
        if (Array.isArray(value) && value.length && typeof value[0] === "object") {
          const rows = value as Record<string, unknown>[];
          const cols = Object.keys(rows[0]).filter((c) => !Array.isArray(rows[0][c]) || c === "roles");
          return (
            <div key={key}>
              <b style={{ textTransform: "capitalize" }}>{title}</b> <small>({rows.length})</small>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", fontSize: 13 }} aria-label={title}>
                  <thead><tr>{cols.map((c) => <th key={c} align="left">{c.replace(/_/g, " ")}</th>)}</tr></thead>
                  <tbody>{rows.map((r, i) => (
                    <tr key={i}>{cols.map((c) => <td key={c}>{show(r[c])}</td>)}</tr>
                  ))}</tbody>
                </table>
              </div>
              {rows.some((r) => Array.isArray(r.services)) && rows.map((r, i) => Array.isArray(r.services) && (r.services as unknown[]).length > 0 && (
                <div key={`s${i}`} style={{ marginTop: 6 }}>
                  <small>Services of {String(r.name)}:</small>
                  <View data={{ services: r.services }} />
                </div>
              ))}
            </div>
          );
        }
        return <div key={key}><b style={{ textTransform: "capitalize" }}>{title}:</b> {show(value)}</div>;
      })}
    </div>
  );
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(show).join(", ") || "—";
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k.replace(/_/g, " ")}: ${show(v)}`).join(" · ");
  }
  if (typeof value === "string" && /^\d{4}-\d\d-\d\dT/.test(value)) return value.slice(0, 16).replace("T", " ");
  return String(value);
}

function Ask({ data, onAsked }: { data: SupportAccessData; onAsked: () => void }) {
  const [customer, setCustomer] = useState("");
  const [ticket, setTicket] = useState("");
  const [reason, setReason] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [minutes, setMinutes] = useState(data.minutes[0] ?? 10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await askSupportAccess({ customer, ticket: ticket.trim(), reason: reason.trim(), scopes, minutes });
      setTicket(""); setReason(""); setScopes([]);
      onAsked();
    } catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Ask a customer for access"
      sub="The customer's administrator is emailed and must approve. The time starts when they approve. Ask only for the views the ticket needs.">
      <form onSubmit={(e) => void submit(e)} style={{ display: "grid", gap: 8, maxWidth: 520 }}>
        <label>Customer
          <select className="field" value={customer} onChange={(e) => setCustomer(e.target.value)} required>
            <option value="">Choose…</option>
            {data.customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label>Ticket number
          <input className="field" value={ticket} onChange={(e) => setTicket(e.target.value)} placeholder="ZD-1042" maxLength={40} required />
        </label>
        <label>Why (the customer reads this; never put patient details here)
          <textarea className="field" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={300} required />
        </label>
        <fieldset style={{ border: 0, padding: 0 }}>
          <legend>What you need to see</legend>
          {data.scopes.map((s) => (
            <label key={s.code} style={{ display: "flex", gap: 8, alignItems: "flex-start", margin: "4px 0" }}>
              <input type="checkbox" checked={scopes.includes(s.code)}
                onChange={(e) => setScopes((cur) => e.target.checked ? [...cur, s.code] : cur.filter((x) => x !== s.code))} />
              <span><b>{s.code}</b>: {s.label}</span>
            </label>
          ))}
        </fieldset>
        <label>For how long
          <select className="field" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
            {data.minutes.map((m) => <option key={m} value={m}>{m} minutes</option>)}
          </select>
        </label>
        {error && <div className="error" role="alert">{error}</div>}
        <div><button className="btn" type="submit" disabled={busy || !customer || !ticket.trim() || reason.trim().length < 10 || !scopes.length}>
          {busy ? "Asking…" : "Ask for access"}</button></div>
      </form>
    </Card>
  );
}

export function SupportAccess() {
  const [data, setData] = useState<SupportAccessData | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<{ row: SupportAccessRow; scope: string; view: Record<string, unknown> } | null>(null);
  const [now, setNow] = useState(Date.now());
  const load = useCallback(async () => {
    try { setData(await getSupportAccess()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => { setNow(Date.now()); void load(); }, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  async function look(row: SupportAccessRow, scope: string) {
    setError("");
    try { const r = await lookSupportAccess(row.customer.id, row.id, scope); setOpen({ row, scope, view: r.view }); void load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open it"); setOpen(null); void load(); }
  }
  async function end(row: SupportAccessRow) {
    setError("");
    try { await endSupportAccess(row.customer.id, row.id); setOpen(null); void load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not end it"); }
  }

  return (
    <>
      <div className="hero"><div>
        <h1>Support access</h1>
        <p>You have no standing access inside a customer. Ask for a few minutes, the customer approves, and every look is recorded on their own audit log. You see setup and status only, never a patient.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {data && <Ask data={data} onAsked={() => void load()} />}
      <Card title="My requests">
        {!data ? <p>Loading…</p> : data.access.length === 0 ? <p>None yet.</p> : (
          <div style={{ display: "grid", gap: 10 }}>
            {data.access.map((r) => (
              <div key={r.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
                <div className="info">
                  <b>{r.customer.name} · {r.ticket}</b>{" "}
                  <Pill kind={STATE[r.state].kind}>{STATE[r.state].text}</Pill>{" "}
                  {minutesLeft(r, now) && <Pill kind="warn">{minutesLeft(r, now)}</Pill>}
                  <small>{r.minutes} minutes · {r.scopes.join(", ")} · asked {show(r.asked_at)}
                    {r.decided_by ? ` · decided by ${r.decided_by}` : ""} · {r.uses} look{r.uses === 1 ? "" : "s"}</small>
                  <small>{r.reason}</small>
                </div>
                {(r.state === "active" || r.state === "requested") && (
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
                    {r.state === "active" && r.scopes.map((s) => (
                      <button key={s} className="btn" type="button" onClick={() => void look(r, s)}>Open {s}</button>
                    ))}
                    <button className="btn ghost" type="button" onClick={() => void end(r)}>
                      {r.state === "active" ? "End now" : "Withdraw"}</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
      {open && (
        <Card title={`${open.row.customer.name}: ${open.scope}`} sub="Recorded on the customer's audit log. Setup and status only.">
          <View data={open.view} />
          <div style={{ marginTop: 8 }}><button className="btn ghost" type="button" onClick={() => setOpen(null)}>Close</button></div>
        </Card>
      )}
    </>
  );
}
