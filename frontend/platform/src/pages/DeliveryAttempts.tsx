import { useCallback, useEffect, useState } from "react";
import { getDeliveryAttempts, type AttemptCustomer } from "../api";
import { Card, Pill } from "../ui";

const STATE: Record<string, { text: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  pending: { text: "Waiting to be sent", kind: "info" },
  retrying: { text: "Will be tried again", kind: "warn" },
  dead_letter: { text: "Gave up: needs the customer", kind: "bad" },
  delivered: { text: "Delivered", kind: "ok" },
};

/** Regional support's view of delivery attempts (GHE-13 T-54, DEV-328): status only, never a message. */
export function DeliveryAttempts() {
  const [rows, setRows] = useState<AttemptCustomer[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try { setRows(await getDeliveryAttempts()); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <Card title="Delivery attempts">
      <p>Messages Zoiko sends to customers' own systems, and FHIR messages held for their staff, across every customer in this region. You see status only: never a message, the address it goes to, or a person. Only the customer can resend a message that gave up. Each look is recorded.</p>
      {error && <div className="error" role="alert">{error}</div>}
      {rows && rows.length === 0 && <p>Nothing to show: no customer has a message waiting or held.</p>}
      {(rows ?? []).map((c) => (
        <div key={c.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <div className="info">
            <b>{c.name} {c.status !== "active" && <Pill kind="warn">{c.status}</Pill>}</b>
            <small>
              {Object.entries(c.counts).filter(([, n]) => n > 0).map(([s, n]) => `${STATE[s]?.text ?? s}: ${n}`).join(" · ") || "No messages sent"}
              {c.fhir_held > 0 ? ` · FHIR messages held for their staff: ${c.fhir_held}` : ""}
            </small>
          </div>
          {c.attempts.length > 0 && (
            <table aria-label={`Open delivery attempts for ${c.name}`} style={{ width: "100%", fontSize: 13 }}>
              <thead><tr><th align="left">Event</th><th align="left">State</th><th align="right">Tries</th><th align="right">Last answer</th><th align="left">Next try</th></tr></thead>
              <tbody>
                {c.attempts.map((a) => (
                  <tr key={a.public_id}>
                    <td>{a.event_type}</td>
                    <td><Pill kind={STATE[a.state]?.kind ?? "info"}>{STATE[a.state]?.text ?? a.state}</Pill></td>
                    <td align="right">{a.attempts}</td>
                    <td align="right">{a.last_status ?? a.last_error ?? "-"}</td>
                    <td>{a.state === "dead_letter" ? "-" : a.next_attempt_at.slice(0, 16).replace("T", " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
      <div style={{ marginTop: 8 }}><button className="btn ghost" onClick={() => void load()}>Refresh</button></div>
    </Card>
  );
}
