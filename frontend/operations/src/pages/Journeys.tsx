import { useState } from "react";
import { getJourneyHistory, getJourneys } from "../api";
import type { JourneyDefinition, JourneyHistory, JourneyInstance, JourneysData } from "../consoleTypes";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, type PillKind } from "../ui";

const KIND: Record<string, string> = {
  transport_receipt: "a delivery receipt",
  authenticated_view: "a viewed screen",
  professional_acknowledgment: "a clinician's acknowledgment",
  source_confirmed_outcome: "the partner's own confirmation",
};

const STATUS: Record<JourneyInstance["status"], { word: string; kind: PillKind }> = {
  running: { word: "Running", kind: "info" },
  completed: { word: "Completed", kind: "ok" },
  cancelled: { word: "Cancelled", kind: "warn" },
};

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "");
const minutes = (m: number) => (m % 1440 === 0 ? `${m / 1440} day${m === 1440 ? "" : "s"}` : m % 60 === 0 ? `${m / 60} hours` : `${m} minutes`);
const words = (s: string) => s.replace(/_/g, " ");

export function Journeys() {
  const result = useLoad(getJourneys, 15_000);
  return <Gate result={result}>{(d) => <View data={d} />}</Gate>;
}

function View({ data }: { data: JourneysData }) {
  return (
    <>
      <div className="hero"><div>
        <h1>Journeys</h1>
        <p>A journey is a step-by-step path (such as book, confirm, attend) with a named owner, deadlines and a
          recorded history. The paths below are fixed once released: a journey that has started keeps the version it
          began on. This page only shows them; it changes nothing.</p>
      </div></div>
      <div className="grid">
        <Card title={`Journey definitions (${data.definitions.length})`} sub="Released versions. They cannot be edited.">
          {data.definitions.length === 0
            ? <div className="empty">No journey has been released yet.</div>
            : data.definitions.map((d) => <Definition key={d.public_id} d={d} />)}
        </Card>
        <Card title={`Your journeys (${data.instances.length})`} sub="Newest first. Open one to see each step and the evidence behind it.">
          {data.instances.length === 0
            ? <div className="empty">No journeys are running for this clinic.</div>
            : data.instances.map((i) => <Instance key={i.public_id} i={i} />)}
        </Card>
      </div>
    </>
  );
}

function Definition({ d }: { d: JourneyDefinition }) {
  const timers = Object.entries(d.timers);
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{d.key} version {d.version} <Pill kind={d.state === "released" ? "ok" : "warn"}>{d.state === "released" ? "Released" : "Retired"}</Pill></b>
        <small>Starts at "{words(d.initial)}". States: {d.states.map(words).join(", ")}.</small>
        <small>Ends at: {Object.entries(d.terminal).map(([s, o]) => `${words(s)} (${o})`).join(", ")}.</small>
        <small>Steps: {d.transitions.map(words).join(", ")}.</small>
        {timers.length > 0 && (
          <small>Deadlines: {timers.map(([n, m]) => `"${words(n)}" after ${minutes(m)}`).join("; ")}. A deadline only raises an exception for a person; it never moves the journey.</small>
        )}
        <small>Released {when(d.released_at)}. Fingerprint {d.body_digest.slice(0, 12)}.</small>
      </div>
    </div>
  );
}

function Instance({ i }: { i: JourneyInstance }) {
  const [open, setOpen] = useState(false);
  const s = STATUS[i.status];
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{i.subject_ref} <small>{i.definition_key} version {i.definition_version}</small></b>
        <small>At "{words(i.state)}" since it started {when(i.created_at)}{i.completed_at ? `; ended ${when(i.completed_at)}` : ""}.</small>
        <span><Pill kind={s.kind}>{s.word}</Pill></span>
      </div>
      <div className="form-row">
        <button className="btn ghost" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Hide steps" : "Show steps"}<span className="sr-only"> of {i.subject_ref}</span>
        </button>
      </div>
      {open && <Steps id={i.public_id} />}
    </div>
  );
}

function Steps({ id }: { id: string }) {
  const result = useLoad<JourneyHistory>(() => getJourneyHistory(id), 60_000);
  return (
    <Gate result={result}>{(h) => (
      <div className="sub">
        <b>{h.chain_ok ? "History checks out" : `History does not check out at step ${h.bad_seq}`}</b>
        <ol>
          {h.steps.map((s) => (
            <li key={s.seq}>
              {when(s.occurred_at)}: {s.from_state ? `${words(s.from_state)} to ${words(s.to_state)}` : `started at ${words(s.to_state)}`}
              {" "}by {s.by}
              {s.evidence.length > 0 && ` with ${s.evidence.map((e) => KIND[e.kind] ?? e.kind).join(" and ")}`}
            </li>
          ))}
        </ol>
      </div>
    )}</Gate>
  );
}
