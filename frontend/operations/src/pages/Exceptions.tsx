import { useState } from "react";
import {
  getConnectorHistory, getExceptions, redriveOperation, resolveJourneyException, resolveOperation,
} from "../api";
import type { ConnectorOp, ExceptionsData, JourneyException, OperationHistory, OperationState } from "../consoleTypes";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, type PillKind } from "../ui";

const STATE: Record<OperationState, { word: string; kind: PillKind }> = {
  pending: { word: "Queued", kind: "info" },
  sent: { word: "Being sent", kind: "info" },
  accepted: { word: "Received by the partner, not yet confirmed", kind: "warn" },
  confirmed: { word: "Confirmed", kind: "ok" },
  rejected: { word: "Refused", kind: "bad" },
  unknown: { word: "Outcome unknown", kind: "warn" },
  dead: { word: "Needs a person", kind: "bad" },
};

const DEADLINE: Record<string, string> = {
  confirm_by: "Not confirmed in time",
  attendance_by: "No attendance recorded in time",
};

const EVENT: Record<string, string> = {
  requested: "Requested", sent: "Sent to the partner", accepted: "Receipt acknowledged",
  confirmed: "Confirmed", rejected: "Refused", unknown: "Outcome unknown", retry: "Will try again",
  dead: "Gave up", redriven: "Put back in the queue", resolved: "Settled by a person",
};

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "");

/** Who stands behind a finished outcome. A person's settlement is never shown as the partner's word. */
function outcome(op: ConnectorOp): string {
  const byHand = op.last_code === "manual_resolution";
  if (op.state === "confirmed") return op.source_confirmed ? "Confirmed by the partner" : "Settled by a person as confirmed";
  if (op.state === "rejected") {
    return byHand ? "Settled by a person as refused" : `Refused by the partner${op.last_code ? ` (${op.last_code})` : ""}`;
  }
  return STATE[op.state].word;
}

export function Exceptions() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getExceptions, 10_000, version);
  return <Gate result={result}>{(d) => <View data={d} onChanged={() => setVersion((v) => v + 1)} />}</Gate>;
}

function View({ data, onChanged }: { data: ExceptionsData; onChanged: () => void }) {
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function run(key: string, work: () => Promise<unknown>, success: string) {
    setBusy(key); setError(""); setNote("");
    try { await work(); setNote(success); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }

  const { attention, waiting, recent } = data.connector;
  const nothing = attention.length === 0 && data.journeys.length === 0;
  return (
    <>
      <div className="hero"><div>
        <h1>Exceptions</h1>
        <p>Work that needs a person. Zoiko never guesses what happened in a partner's system: when an answer is
          missing, the operation waits here as "unknown" until the partner is asked again or a person settles it.
          A receipt from a partner is not a confirmation.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
      {!data.can_act && <div className="notice" role="status">Your role can look at this page but not act on it.</div>}
      <div className="grid">
        <Card title={`Needs a person (${attention.length + data.journeys.length})`}
          sub="Partner operations whose outcome is unknown or that gave up, and journey deadlines that ran out.">
          {nothing && <div className="empty">Nothing needs a person right now.</div>}
          {attention.map((op) => (
            <AttentionRow key={op.public_id} op={op} canAct={data.can_act} busy={busy === op.public_id}
              onRedrive={() => void run(op.public_id, () => redriveOperation(op.public_id), "Put back in the queue.")}
              onSettle={(o) => void run(op.public_id, () => resolveOperation(op.public_id, o),
                o === "confirmed" ? "Recorded as confirmed by you." : "Recorded as refused by you.")} />
          ))}
          {data.journeys.map((j) => (
            <JourneyRow key={j.public_id} item={j} canAct={data.can_act} busy={busy === j.public_id}
              onResolve={() => void run(j.public_id, () => resolveJourneyException(j.public_id), "Marked as dealt with.")} />
          ))}
        </Card>
        <Card title={`In flight (${waiting.length})`} sub="Sent or waiting for the partner. Nothing to do yet.">
          {waiting.length === 0 ? <div className="empty">Nothing is waiting on a partner.</div> : waiting.map((op) => (
            <div key={op.public_id} className="appt">
              <div className="info">
                <b>{op.operation_type} <small>for {op.subject_ref}</small></b>
                <small>Tried {op.attempts} time{op.attempts === 1 ? "" : "s"}; asked the partner {op.polls} time{op.polls === 1 ? "" : "s"}.</small>
              </div>
              <Pill kind={STATE[op.state].kind}>{STATE[op.state].word}</Pill>
            </div>
          ))}
        </Card>
        <Card title="Recently finished" sub="The last few outcomes, and who stands behind each.">
          {recent.length === 0 ? <div className="empty">Nothing has finished yet.</div> : recent.map((op) => (
            <div key={op.public_id} className="appt">
              <div className="info">
                <b>{op.operation_type} <small>for {op.subject_ref}</small></b>
                <small>{when(op.finished_at)}</small>
              </div>
              <Pill kind={STATE[op.state].kind}>{outcome(op)}</Pill>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}

function AttentionRow({ op, canAct, busy, onRedrive, onSettle }: {
  op: ConnectorOp; canAct: boolean; busy: boolean; onRedrive: () => void; onSettle: (o: "confirmed" | "rejected") => void;
}) {
  const [showHistory, setShowHistory] = useState(false);
  const [settling, setSettling] = useState(false);
  const label = `${op.operation_type} for ${op.subject_ref}`;
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{op.operation_type} <small>for {op.subject_ref}</small></b>
        <small>
          {op.state === "unknown"
            ? "The partner did not answer, so we do not know whether it happened. It will not be sent again until the partner has been asked."
            : "It kept failing and was given up. Nothing has been confirmed."}
          {" "}Tried {op.attempts} time{op.attempts === 1 ? "" : "s"}; asked the partner {op.polls} time{op.polls === 1 ? "" : "s"}.
          {op.last_error && ` Last problem: ${op.last_error}.`}
        </small>
        <span><Pill kind={STATE[op.state].kind}>{STATE[op.state].word}</Pill></span>
      </div>
      <div className="form-row">
        <button className="btn ghost" aria-expanded={showHistory} onClick={() => setShowHistory((s) => !s)}>
          {showHistory ? "Hide history" : "Show history"}<span className="sr-only"> of {label}</span>
        </button>
        {canAct && op.state === "dead" && (
          <button className="btn" disabled={busy} onClick={onRedrive}>Try again<span className="sr-only"> {label}</span></button>
        )}
        {canAct && !settling && (
          <button className="btn ghost" disabled={busy} onClick={() => setSettling(true)}>Settle by hand<span className="sr-only"> {label}</span></button>
        )}
      </div>
      {settling && (
        <div className="notice" role="group" aria-label={`Settle ${label} by hand`}>
          Only do this after you have checked with the partner yourself. It is recorded under your name, and it is
          never shown as the partner's own confirmation.
          <div className="form-row">
            <button className="btn" disabled={busy} onClick={() => { setSettling(false); onSettle("confirmed"); }}>Record as confirmed</button>
            <button className="btn" disabled={busy} onClick={() => { setSettling(false); onSettle("rejected"); }}>Record as refused</button>
            <button className="btn ghost" onClick={() => setSettling(false)}>Cancel</button>
          </div>
        </div>
      )}
      {showHistory && <History id={op.public_id} />}
    </div>
  );
}

function History({ id }: { id: string }) {
  const result = useLoad<OperationHistory>(() => getConnectorHistory(id), 60_000);
  return (
    <Gate result={result}>{(h) => (
      <div className="sub">
        <b>{h.chain_ok ? "History checks out" : `History does not check out at step ${h.bad_seq}`}</b>
        <ol>
          {h.events.map((e) => (
            <li key={e.seq}>{when(e.occurred_at)}: {EVENT[e.event_type] ?? e.event_type}{e.code ? ` (${e.code})` : ""} by {e.by}</li>
          ))}
        </ol>
      </div>
    )}</Gate>
  );
}

function JourneyRow({ item, canAct, busy, onResolve }: {
  item: JourneyException; canAct: boolean; busy: boolean; onResolve: () => void;
}) {
  const what = DEADLINE[item.detail] ?? item.detail;
  return (
    <div className="appt">
      <div className="info">
        <b>{what} <small>for {item.subject_ref}</small></b>
        <small>{item.journey}, still at "{item.journey_state}". The deadline passed {when(item.raised_at)}. Nothing was changed for you: a person decides what happens next.</small>
      </div>
      <Pill kind="warn">Deadline passed</Pill>
      {canAct && <button className="btn ghost" disabled={busy} onClick={onResolve}>Mark as dealt with<span className="sr-only"> {what} for {item.subject_ref}</span></button>}
    </div>
  );
}
