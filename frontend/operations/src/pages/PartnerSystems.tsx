import { useState } from "react";
import {
  getBookingSystem, getCircuits, getPartnerOperations, redriveOperation, resetCircuit,
  resolveOperation, setBookingSystem, type ConnectorCircuit, type PartnerOperation, type Staff,
} from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

const STATE: Record<PartnerOperation["state"], { text: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  pending: { text: "Queued", kind: "info" },
  sent: { text: "Being sent", kind: "info" },
  accepted: { text: "Received by the partner (not yet confirmed)", kind: "info" },
  confirmed: { text: "Confirmed", kind: "ok" },
  rejected: { text: "Refused", kind: "bad" },
  unknown: { text: "Outcome unknown", kind: "warn" },
  dead: { text: "Gave up: needs a person", kind: "bad" },
};

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : "";
}

/** Which system confirms this clinic's bookings. Only the administrator changes it (DEV-217). */
function BookingSystemCard() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getBookingSystem, 60_000, version);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function choose(system: string) {
    setBusy(true); setError("");
    try { await setBookingSystem(system); setVersion((v) => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Gate result={result}>
      {(s) => (
        <Card title="Booking system" sub="Who confirms a patient's booking. With a partner system chosen, a booking stays requested until that system itself confirms it; staff can no longer confirm by hand.">
          {s.can_set ? (
            <label>Confirmed by
              <select value={s.system} disabled={busy} onChange={(e) => void choose(e.target.value)} aria-label="Booking system">
                {s.options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
              </select>
            </label>
          ) : (
            <p>{s.options.find((o) => o.key === s.system)?.label ?? s.system} <small>(only your clinic administrator can change this)</small></p>
          )}
          {error && <div className="error" role="alert">{error}</div>}
        </Card>
      )}
    </Gate>
  );
}

const CIRCUIT: Record<ConnectorCircuit["state"], { text: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  closed: { text: "Working", kind: "ok" },
  open: { text: "Paused after failures", kind: "warn" },
  trial: { text: "Trying one call", kind: "info" },
  halted: { text: "Stopped: the partner does not support what we send", kind: "bad" },
};

/** Which partner systems are paused or stopped (DEV-221). Work waits; nothing is lost. */
function Circuits() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getCircuits, 30_000, version);
  const [error, setError] = useState("");
  async function reset(key: string) {
    setError("");
    try { await resetCircuit(key); setVersion((v) => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
  }
  return (
    <Gate result={result}>
      {(r) => (
        <Card title="Partner system health" sub="After repeated failures a partner is left alone for a while and its work waits here. While your booking system is paused or stopped, staff may confirm bookings by hand.">
          <Table headers={["System", "State", "Failures in a row", "Last code", "Paused until", ""]} empty="Every partner system is working."
            rows={r.circuits.map((c) => [c.connector_key, <Pill key="s" kind={CIRCUIT[c.state].kind}>{CIRCUIT[c.state].text}</Pill>,
              String(c.failures), c.last_code, when(c.open_until),
              r.can_act && c.state !== "closed" ? <button key="b" className="btn ghost" onClick={() => void reset(c.connector_key)}>Start again</button> : ""])} />
          {error && <div className="error" role="alert">{error}</div>}
        </Card>
      )}
    </Gate>
  );
}

function Actions({ op, onDone }: { op: PartnerOperation; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <span>
      {op.state === "dead" && <button className="btn" disabled={busy} onClick={() => void run(() => redriveOperation(op.public_id))}>Try again</button>}{" "}
      <button className="btn ghost" disabled={busy} title="Only after checking with the partner. A booking stays requested: this is not the partner's own confirmation."
        onClick={() => void run(() => resolveOperation(op.public_id, "confirmed"))}>Partner says done</button>{" "}
      <button className="btn ghost" disabled={busy} onClick={() => void run(() => resolveOperation(op.public_id, "rejected"))}>Partner says no</button>
      {error && <span className="error" role="alert"> {error}</span>}
    </span>
  );
}

export function PartnerSystems({ staff }: { staff: Staff | null }) {
  const [version, setVersion] = useState(0);
  const result = useLoad(getPartnerOperations, 30_000, version);
  if (!staff) return <NoStaff />;
  const row = (op: PartnerOperation, act: boolean) => [
    op.operation_type, <Pill key="s" kind={STATE[op.state].kind}>{STATE[op.state].text}</Pill>,
    `${op.attempts} tries${op.polls ? `, asked ${op.polls} times` : ""}`, op.last_code || op.last_error || "",
    when(op.created_at), act ? <Actions key="a" op={op} onDone={() => setVersion((v) => v + 1)} /> : "",
  ];
  return (
    <>
      <div className="hero"><div>
        <h1>Partner systems</h1>
        <p>What {staff.organizationName} has asked partner systems to do. A receipt is not an answer: only the partner's own confirmation counts, and an unknown outcome is asked again, never guessed.</p>
      </div></div>
      <div className="grid">
        <BookingSystemCard />
        <Circuits />
        <Gate result={result}>
          {(r) => (
            <>
              <Card title={`Needs a person (${r.attention.length})`} sub="The outcome is unknown or the connector gave up. Check with the partner before settling it.">
                <Table headers={["What", "State", "Tries", "Last code", "Asked", ""]} empty="Nothing needs a person."
                  rows={r.attention.map((op) => row(op, r.can_act))} />
              </Card>
              <Card title={`Waiting for the partner (${r.waiting.length})`}>
                <Table headers={["What", "State", "Tries", "Last code", "Asked", ""]} empty="Nothing is waiting."
                  rows={r.waiting.map((op) => row(op, false))} />
              </Card>
              <Card title="Recent answers">
                <Table headers={["What", "State", "Tries", "Last code", "Asked", ""]} empty="No answers yet."
                  rows={r.recent.map((op) => row(op, false))} />
              </Card>
            </>
          )}
        </Gate>
      </div>
    </>
  );
}
