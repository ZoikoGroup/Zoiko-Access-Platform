import { useCallback, useEffect, useState } from "react";
import { addStandardJourney, getWorkflowHistory, listEscalations, listWorkflowDefinitions, listWorkflows, recordEscalation } from "../api";
import type { Escalation, TimerPolicy, WorkflowDefinitions, WorkflowHistory, WorkflowInstance } from "../types";
import { Card, Pill, type PillKind } from "../ui";

const STATE_TEXT: Record<string, string> = {
  requested: "Requested", validated: "Validated", accepted: "Accepted", in_progress: "In progress",
  output_available: "Output available", clinical_reviewed: "Reviewed by a clinician",
  handoff_acknowledged: "Next party accepted", closed: "Closed", cancelled: "Cancelled", blocked: "Blocked",
};
const OVERLAY: Record<string, { text: string; kind: PillKind }> = {
  clear: { text: "Clear", kind: "ok" }, at_risk: { text: "At risk", kind: "warn" },
  exception: { text: "Exception", kind: "bad" }, reconciling: { text: "Checking with the source", kind: "info" },
};

function Timeline({ id }: { id: string }) {
  const [data, setData] = useState<WorkflowHistory | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getWorkflowHistory(id).then(setData).catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, [id]);
  if (error) return <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>;
  if (!data) return <small>Loading…</small>;
  return (
    <div className="info">
      <small>{data.chain_ok ? "The history checks out." : `The history does NOT check out from step ${data.bad_seq}.`}</small>
      {data.steps.map((s) => (
        <small key={s.seq}>
          {s.seq}. {s.kind === "transition" ? `${STATE_TEXT[s.from_state] ?? "Start"} → ${STATE_TEXT[s.to_state]}`
            : s.kind === "overlay" ? `Health set to ${OVERLAY[s.overlay]?.text ?? s.overlay}`
            : s.kind === "timer" ? "A timer ran out" : "A new person was made accountable"}
          {" "}by {s.actor_name}, {s.occurred_at.slice(0, 16).replace("T", " ")}.
          {s.evidence.length > 0 && ` Evidence: ${s.evidence.map((e) => e.kind).join(", ")}.`}
          {s.note && s.kind !== "owner" ? ` “${s.note}”` : ""}
        </small>
      ))}
    </div>
  );
}

function InstanceRow({ w }: { w: WorkflowInstance }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{w.definition} v{w.definition_version} <Pill kind="info">{STATE_TEXT[w.state]}</Pill> <Pill kind={OVERLAY[w.overlay].kind}>{OVERLAY[w.overlay].text}</Pill></b>
        <small>Reference {w.subject_ref}. Accountable: {w.owner_name || "unknown"}{w.owner_inactive ? " (can no longer act: reassign)" : ""}.{w.open_escalations > 0 ? ` ${w.open_escalations} escalation${w.open_escalations === 1 ? "" : "s"} waiting for a person.` : ""}{w.timers.length > 0 ? ` Next timer: ${w.timers[0].due_at.slice(0, 16).replace("T", " ")}.` : ""}</small>
      </div>
      <div className="form-row">
        <button className="btn ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide the history" : "Show the history"}</button>
      </div>
      {open && <Timeline id={w.public_id} />}
    </div>
  );
}

function EscalationRow({ e, onDone }: { e: Escalation; onDone: () => Promise<void> }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(reached: boolean) {
    setBusy(true); setError("");
    try { await recordEscalation(e.public_id, reached, note); setNote(""); await onDone(); }
    catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>Escalation {e.public_id.slice(0, 8)}</b>
        <small>Opened {e.created_at.slice(0, 16).replace("T", " ")}. Tries that reached nobody: {e.attempts}. A timer running out or a message being sent is not proof that anyone was reached: record what actually happened.</small>
        {error && <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      <div className="form-row">
        <input aria-label={`What happened for escalation ${e.public_id.slice(0, 8)}`} placeholder="Who you spoke to, or what you tried" value={note} maxLength={300} onChange={(ev) => setNote(ev.target.value)} />
        <button className="btn" disabled={busy || note.trim().length < 3} onClick={() => void run(true)}>A person took ownership</button>
        <button className="btn ghost" disabled={busy || note.trim().length < 3} onClick={() => void run(false)}>Could not reach anyone</button>
      </div>
    </div>
  );
}

const SETTING_TEXT: Record<string, string> = {
  response_target: "your response target (in your opening hours)",
  offer_window: "the time you give a person to use an offered time",
};

function timerText(t: TimerPolicy) {
  const when = "after_minutes" in t ? `${t.after_minutes} minutes`
    : `${t.times > 1 ? `${t.times} times ` : ""}${SETTING_TEXT[t.after_setting] ?? t.after_setting}`;
  return `${STATE_TEXT[t.state] ?? t.state}: escalate after ${when}`;
}

/** The referral and waiting-list journeys (DEV-394). Deadlines follow the clinic's own settings, so
 *  changing the response target or offer window changes them for new steps. */
function StandardJourneys() {
  const [data, setData] = useState<WorkflowDefinitions | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await listWorkflowDefinitions()); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the journeys"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function add(template: string) {
    setBusy(template); setError("");
    try { await addStandardJourney(template); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }
  if (!data) return null;
  return (
    <Card title="Standard journeys" sub="Referrals and the waiting list. Once published, each new referral or waiting-list entry starts one by itself, for the doctor who answers referrals (or you). Deadlines come from your own settings, never from a fixed number.">
      {error && <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>}
      {data.templates.map((t) => {
        const have = data.definitions.find((d) => d.key === t.key && d.status === "published");
        return (
          <div key={t.template} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <div className="info">
              <b>{t.label} {have && <Pill kind="ok">In use (v{have.version})</Pill>}</b>
              {t.timer_policies.map((p, i) => <small key={i}>{timerText(p)}.</small>)}
            </div>
            {!have && data.can_write && (
              <div className="form-row">
                <button className="btn" disabled={busy !== ""} onClick={() => void add(t.template)}>
                  {busy === t.template ? "Adding…" : "Add and publish"}
                </button>
              </div>
            )}
          </div>
        );
      })}
      {!data.can_write && <small>Only a clinic administrator can add a journey.</small>}
    </Card>
  );
}

/** The workflow engine: what needs a person, everything running, and each one's history. */
export function Engine() {
  const [needs, setNeeds] = useState<WorkflowInstance[] | null>(null);
  const [all, setAll] = useState<WorkflowInstance[]>([]);
  const [escs, setEscs] = useState<Escalation[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setNeeds(await listWorkflows(true)); setAll(await listWorkflows(false)); setEscs(await listEscalations());
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load the workflows"); }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);
  return (
    <>
      <div className="hero"><div>
        <h1>Workflow engine</h1>
        <p>Multi-step work that moves forward only on evidence, with a named accountable person for every step. A step is never marked done because time passed, a message was delivered or a partner was silent. "Closed" means the agreed administrative steps have evidence; it does not mean anyone recovered or was treated. The history of each workflow cannot be changed.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Needs a person (${needs?.length ?? 0})`} sub="At risk, in exception, blocked, or with an escalation waiting.">
          {escs.map((e) => <EscalationRow key={e.public_id} e={e} onDone={load} />)}
          {needs === null ? <div className="empty">Loading…</div> : needs.length === 0 && escs.length === 0
            ? <div className="empty">Nothing needs attention.</div> : needs.map((w) => <InstanceRow key={w.public_id} w={w} />)}
        </Card>
        <Card title={`Everything (${all.length})`}>
          {all.length === 0 ? <div className="empty">No workflow has been started.</div> : all.map((w) => <InstanceRow key={w.public_id} w={w} />)}
        </Card>
        <StandardJourneys />
      </div>
    </>
  );
}
