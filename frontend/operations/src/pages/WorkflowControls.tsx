import { useCallback, useEffect, useState } from "react";
import {
  ApiError, createDefinition, definitionStep, getDirectory, listDefinitions, startWorkflow, workflowStep,
  type WorkflowDefinition,
} from "../api";
import type { Directory, WorkflowInstance } from "../types";
import { Card, Pill, type PillKind } from "../ui";

export const STATE_TEXT: Record<string, string> = {
  requested: "Requested", validated: "Validated", accepted: "Accepted", in_progress: "In progress",
  output_available: "Output available", clinical_reviewed: "Reviewed by a clinician",
  handoff_acknowledged: "Next party accepted", closed: "Closed", cancelled: "Cancelled", blocked: "Blocked",
};
/** The proof each move needs, as the server names it, and as a person would say it. */
const PROOF: Record<string, { kind: string; ask: string }> = {
  validated: { kind: "validation", ask: "Reference of the check that validated it" },
  accepted: { kind: "acceptance_attestation", ask: "Reference of the acceptance" },
  in_progress: { kind: "work_start", ask: "Reference showing work started" },
  output_available: { kind: "output_reference", ask: "Reference of the output" },
  clinical_reviewed: { kind: "review_attestation", ask: "Reference of the clinician's review" },
  handoff_acknowledged: { kind: "handoff_acceptance", ask: "Reference of the next party's acceptance" },
  closed: { kind: "closure_evidence", ask: "Reference of the closing evidence" },
  cancelled: { kind: "cancellation_authorization", ask: "Reference of the authority to cancel" },
  blocked: { kind: "block_reason", ask: "Reference of what blocks it" },
};
const REQUIRED = ["requested", "validated", "accepted", "in_progress", "output_available", "closed"];
const OPTIONAL = ["clinical_reviewed", "handoff_acknowledged"];
const ORDER = ["requested", "validated", "accepted", "in_progress", "output_available",
  "clinical_reviewed", "handoff_acknowledged", "closed"];
const STATUS: Record<string, { text: string; kind: PillKind }> = {
  draft: { text: "Draft", kind: "warn" }, published: { text: "Released", kind: "ok" }, retired: { text: "Retired", kind: "info" },
};

export function problem(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "Not allowed: only the accountable person or an administrator may do this, and a clinical step may be switched off here."
    : code === "CONFLICT_VERSION" ? "That is not possible now: it may have changed meanwhile, the name may be taken, or this reference already has a running workflow."
    : code === "VALIDATION_FAILED" ? "Check what you typed. A reference is 4 to 64 letters, digits, dots, colons, dashes or underscores, with no spaces. A note is at least 3 characters."
    : code === "JURISDICTION_INACTIVE" ? "The country rules in force do not allow this workflow."
    : e instanceof Error ? e.message : "That did not work";
}
const key = () => crypto.randomUUID();

/** People of this clinic who can be made accountable for work. */
export function usePeople(): { id: string; label: string }[] {
  const [people, setPeople] = useState<{ id: string; label: string }[]>([]);
  useEffect(() => {
    getDirectory().then((d: Directory) => setPeople(d.staff.filter((s) => s.status === "active")
      .map((s) => ({ id: s.id, label: s.name || s.login })))).catch(() => setPeople([]));
  }, []);
  return people;
}

function Who({ label, value, onChange, people }: {
  label: string; value: string; onChange: (v: string) => void; people: { id: string; label: string }[];
}) {
  return (
    <label>{label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a person</option>
        {people.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
    </label>
  );
}

type Action = "next" | "block" | "cancel" | "resume" | "health" | "owner";

/** What can be done with one running workflow. Each move asks for the proof the server needs. */
export function InstanceActions({ w, people, onDone }: {
  w: WorkflowInstance; people: { id: string; label: string }[]; onDone: () => Promise<void>;
}) {
  const [action, setAction] = useState<Action | "">("");
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [who, setWho] = useState("");
  const [health, setHealth] = useState("at_risk");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (w.state === "closed" || w.state === "cancelled") return null;
  const blocked = w.state === "blocked";
  const next = blocked ? "" : w.path[w.path.indexOf(w.state) + 1] ?? "";
  const clearing = health === "clear" && (w.overlay === "exception" || w.overlay === "reconciling");
  const open = (a: Action) => { setAction(action === a ? "" : a); setRef(""); setNote(""); setWho(""); setError(""); };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    const base = { expected_version: w.version };
    const proof = (kind: string) => [{ kind, ref }];
    try {
      if (action === "next") await workflowStep(w.public_id, "advance", { ...base, to_state: next, evidence: proof(PROOF[next].kind), idempotency_key: key() });
      if (action === "block") await workflowStep(w.public_id, "advance", { ...base, to_state: "blocked", evidence: proof(PROOF.blocked.kind), note, incident_owner: who, idempotency_key: key() });
      if (action === "cancel") await workflowStep(w.public_id, "advance", { ...base, to_state: "cancelled", evidence: proof(PROOF.cancelled.kind), note, idempotency_key: key() });
      if (action === "resume") await workflowStep(w.public_id, "resume", { ...base, evidence: proof("block_resolution"), note });
      if (action === "health") await workflowStep(w.public_id, "overlay", { ...base, overlay: health, note, ...(clearing ? { evidence: proof("exception_resolution") } : {}) });
      if (action === "owner") await workflowStep(w.public_id, "reassign", { ...base, owner: who });
      setAction(""); await onDone();
    } catch (err) { setError(problem(err)); await onDone(); }
    finally { setBusy(false); }
  }

  const needsRef = action === "next" || action === "block" || action === "cancel" || action === "resume" || (action === "health" && clearing);
  const needsNote = action === "block" || action === "cancel" || action === "resume" || action === "health";
  const ready = (!needsRef || ref.trim().length >= 4) && (!needsNote || note.trim().length >= 3)
    && (!(action === "block" || action === "owner") || who !== "");
  return (
    <div style={{ width: "100%" }}>
      <div className="row">
        {next && <button type="button" className="btn" aria-expanded={action === "next"} onClick={() => open("next")}>Move to “{STATE_TEXT[next]}”</button>}
        {blocked && <button type="button" className="btn" aria-expanded={action === "resume"} onClick={() => open("resume")}>Lift the block</button>}
        {!blocked && <button type="button" className="btn ghost" aria-expanded={action === "block"} onClick={() => open("block")}>Block</button>}
        <button type="button" className="btn ghost" aria-expanded={action === "health"} onClick={() => open("health")}>Set health</button>
        <button type="button" className="btn ghost" aria-expanded={action === "owner"} onClick={() => open("owner")}>Change who is accountable</button>
        <button type="button" className="btn ghost" aria-expanded={action === "cancel"} onClick={() => open("cancel")}>Cancel it</button>
      </div>
      {action && (
        <form className="profile-form" style={{ marginTop: 12 }} onSubmit={(e) => void submit(e)}>
          {action === "health" && (
            <label>Health
              <select value={health} onChange={(e) => setHealth(e.target.value)}>
                <option value="clear">Clear</option><option value="at_risk">At risk</option>
                <option value="exception">Exception</option><option value="reconciling">Checking with the source</option>
              </select>
            </label>
          )}
          {(action === "block" || action === "owner") && <Who label={action === "block" ? "Who owns the problem" : "Who is now accountable"} value={who} onChange={setWho} people={people} />}
          {needsRef && (
            <label>{action === "next" ? PROOF[next].ask : action === "block" ? PROOF.blocked.ask : action === "cancel" ? PROOF.cancelled.ask : action === "resume" ? "Reference of how the block was resolved" : "Reference of how the problem was resolved"}
              <input value={ref} maxLength={64} onChange={(e) => setRef(e.target.value.trim())} />
            </label>
          )}
          {needsNote && <label>Note<input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} /></label>}
          <div className="row">
            <button className="btn" type="submit" disabled={busy || !ready}>{busy ? "Saving…" : "Save"}</button>
            <button type="button" className="btn ghost" onClick={() => setAction("")}>Close</button>
          </div>
        </form>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}

/** Definitions (draft, release, new version, retire) and starting a workflow from a released one. */
export function WorkflowSetup({ people, onStarted }: { people: { id: string; label: string }[]; onStarted: () => Promise<void> }) {
  const [rows, setRows] = useState<WorkflowDefinition[] | null>(null);
  const [canWrite, setCanWrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [draft, setDraft] = useState({ key: "", journey: "", optional: [] as string[] });
  const [start, setStart] = useState({ definition: "", subject: "", owner: "", ref: "" });

  const load = useCallback(async () => {
    try { const got = await listDefinitions(); setRows(got.definitions); setCanWrite(got.can_write); }
    catch (e) { setError(problem(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<unknown>, said: string) {
    setBusy(true); setError(""); setDone("");
    try { await work(); setDone(said); await load(); return true; }
    catch (e) { setError(problem(e)); return false; }
    finally { setBusy(false); }
  }
  const released = [...new Map((rows ?? []).filter((d) => d.status === "published").map((d) => [d.key, d])).values()];

  return (
    <>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">{done}</div>}
      <Card title="Start a workflow" sub="From a released definition. It needs a named accountable person and a reference showing where the request came from.">
        {released.length === 0 ? <div className="empty">No released definition yet. Release one below first.</div> : (
          <form className="profile-form" onSubmit={(e) => { e.preventDefault(); void run(async () => {
            await startWorkflow({ definition: start.definition, subject: start.subject, owner: start.owner,
              evidence: [{ kind: "request_origin", ref: start.ref }], purpose: "OPERATIONS", idempotency_key: key() });
            setStart({ definition: "", subject: "", owner: "", ref: "" }); await onStarted();
          }, "The workflow has started."); }}>
            <label>Definition
              <select value={start.definition} onChange={(e) => setStart({ ...start, definition: e.target.value })}>
                <option value="">Choose a definition</option>
                {released.map((d) => <option key={d.public_id} value={d.key}>{d.key} v{d.version}</option>)}
              </select>
            </label>
            <label>What it is about (your own reference, no names)
              <input value={start.subject} maxLength={40} onChange={(e) => setStart({ ...start, subject: e.target.value.trim() })} />
            </label>
            <Who label="Accountable person" value={start.owner} onChange={(owner) => setStart({ ...start, owner })} people={people} />
            <label>Reference of where the request came from
              <input value={start.ref} maxLength={64} onChange={(e) => setStart({ ...start, ref: e.target.value.trim() })} />
            </label>
            <div><button className="btn" type="submit" disabled={busy || !start.definition || start.subject.length < 4 || !start.owner || start.ref.length < 4}>Start</button></div>
          </form>
        )}
      </Card>
      <Card title={`Definitions (${rows?.length ?? 0})`} sub="The steps a kind of work follows. A released definition never changes: a change is a new version.">
        {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">No definition yet.</div> : rows.map((d) => (
          <div key={d.public_id} className="appt">
            <div className="info">
              <b>{d.key} v{d.version} <Pill kind={STATUS[d.status]?.kind ?? "info"}>{STATUS[d.status]?.text ?? d.status}</Pill>{d.clinical && <> <Pill kind="info">has clinical steps</Pill></>}</b>
              <small>{d.path.map((s) => STATE_TEXT[s] ?? s).join(" → ")}</small>
            </div>
            {canWrite && (
              <div className="row">
                {d.status === "draft" && <button className="btn" disabled={busy} onClick={() => void run(() => definitionStep(d.public_id, "publish"), `${d.key} v${d.version} is released.`)}>Release</button>}
                {d.status === "published" && <button className="btn ghost" disabled={busy} onClick={() => void run(() => definitionStep(d.public_id, "version"), "A new draft version was made.")}>New version</button>}
                {d.status === "published" && <button className="btn ghost" disabled={busy} onClick={() => void run(() => definitionStep(d.public_id, "retire"), `${d.key} v${d.version} is retired.`)}>Retire</button>}
              </div>
            )}
          </div>
        ))}
        {canWrite ? (
          <form className="profile-form" style={{ marginTop: 16 }} onSubmit={(e) => { e.preventDefault(); void run(async () => {
            const path = ORDER.filter((s) => REQUIRED.includes(s) || draft.optional.includes(s));
            await createDefinition({ key: draft.key, journey: draft.journey, path, timer_policies: [] });
            setDraft({ key: "", journey: "", optional: [] });
          }, "Saved as a draft. Release it to use it."); }}>
            <label>Name of a new definition (small letters, digits and dashes)
              <input value={draft.key} maxLength={63} onChange={(e) => setDraft({ ...draft, key: e.target.value.toLowerCase().trim() })} />
            </label>
            <label>Kind of journey (small letters, digits and underscores)
              <input value={draft.journey} maxLength={63} onChange={(e) => setDraft({ ...draft, journey: e.target.value.toLowerCase().trim() })} />
            </label>
            <fieldset><legend>Extra steps (the rest are always included)</legend>
              {OPTIONAL.map((s) => (
                <label key={s} className="check"><input type="checkbox" checked={draft.optional.includes(s)}
                  onChange={(e) => setDraft({ ...draft, optional: e.target.checked ? [...draft.optional, s] : draft.optional.filter((x) => x !== s) })} /> {STATE_TEXT[s]}</label>
              ))}
            </fieldset>
            <div><button className="btn" type="submit" disabled={busy || draft.key.length < 3 || draft.journey.length < 2}>Save draft</button></div>
          </form>
        ) : <small>Only your clinic administrator can write or release definitions.</small>}
      </Card>
    </>
  );
}
