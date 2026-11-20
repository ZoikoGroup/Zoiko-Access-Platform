import { useState } from "react";
import { decideMatch, getMatches } from "../api";
import type { MatchCase, MatchFactor, MatchesData, MatchState } from "../consoleTypes";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, type PillKind } from "../ui";

const STATE: Record<MatchState, { text: string; kind: PillKind }> = {
  possible_match: { text: "Needs a decision", kind: "warn" },
  matched_pending_auth: { text: "One reviewer agrees: a second is needed", kind: "warn" },
  linked: { text: "Confirmed the same person", kind: "info" },
  split_review: { text: "Link sent back for review", kind: "warn" },
  closed_not_same: { text: "Closed: not the same person", kind: "ok" },
};

const FACTOR: Record<MatchFactor, string> = {
  same: "Same", close: "Almost the same", transposed: "Looks like a slip (digits swapped)",
  differ: "Different", missing: "Not recorded",
};

const FLAG: Record<string, string> = {
  twin: "Possible twins or siblings: same birth date and family name",
  transposition: "A birth date that may have been copied wrongly",
  name_change: "Same birth date and phone but a different name (a changed name?)",
  shared_phone: "The same phone number, a different birth date",
};

const ACTION: Record<string, string> = {
  opened: "Held for review", request_recheck: "Asked for the details to be checked again",
  confirm_same: "Agreed they are the same person", mark_not_same: "Decided they are not the same person",
  split: "Sent the link back for review", reopened: "Reopened because the details changed",
};

const NOTES: [string, string][] = [
  ["", "No reason given"], ["same_details", "The details are the same"],
  ["different_people", "They are different people"], ["twins", "They are twins or siblings"],
  ["data_entry_error", "A data entry mistake"], ["other", "Another reason"],
];

const when = (iso: string) => new Date(iso).toLocaleString();
const isOpen = (c: MatchCase) => c.state === "possible_match" || c.state === "matched_pending_auth" || c.state === "split_review";

export function Matches() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getMatches, 15_000, version);
  return <Gate result={result}>{(d) => <View data={d} onChanged={() => setVersion((v) => v + 1)} />}</Gate>;
}

function View({ data, onChanged }: { data: MatchesData; onChanged: () => void }) {
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const open = data.cases.filter(isOpen);
  const settled = data.cases.filter((c) => !isOpen(c));

  async function run(id: string, action: string, reason: string, success: string) {
    setBusy(id); setError(""); setNote("");
    try { await decideMatch(id, action, reason); setNote(success); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }

  return (
    <>
      <div className="hero"><div>
        <h1>Duplicates</h1>
        <p>Two patient records that might be the same person are held here for a person to decide. Nothing is ever
          merged automatically, and nothing is merged at all: a confirmed link only records the decision. Details are
          masked on purpose (initials, birth year, the last two phone digits). While a case is open, neither
          patient can have their identity checked.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
      {!data.can_decide && <div className="notice" role="status">Your role can look at this page but not decide cases.</div>}
      <div className="grid">
        <Card title={`Needs a decision (${open.length})`} sub="Cases that are critical (twins, a slipped date, or a patient with a doctor or guardian) need two different reviewers.">
          {open.length === 0 ? <div className="empty">No possible duplicates are waiting.</div>
            : open.map((c) => <Case key={c.public_id} c={c} canDecide={data.can_decide} busy={busy === c.public_id} run={run} />)}
        </Card>
        <Card title={`Settled (${settled.length})`} sub="Confirmed links, and cases closed in the last month.">
          {settled.length === 0 ? <div className="empty">Nothing settled recently.</div>
            : settled.map((c) => <Case key={c.public_id} c={c} canDecide={data.can_decide} busy={busy === c.public_id} run={run} />)}
        </Card>
      </div>
    </>
  );
}

function Case({ c, canDecide, busy, run }: {
  c: MatchCase; canDecide: boolean; busy: boolean;
  run: (id: string, action: string, reason: string, success: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [asking, setAsking] = useState<"" | "same" | "not_same" | "split">("");
  const [history, setHistory] = useState(false);
  const s = STATE[c.state];
  const label = `case ${c.ref_no}`;
  const pending = c.state === "matched_pending_auth";
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{c.ref_no} <Pill kind={s.kind}>{s.text}</Pill>{" "}
          <Pill kind={c.confidence === "high" ? "bad" : "info"}>{c.confidence === "high" ? "High similarity" : "Medium similarity"}</Pill>
          {c.critical && <>{" "}<Pill kind="bad">Critical: two reviewers</Pill></>}</b>
        <small>Opened {when(c.created_at)}.{c.recheck_requested && " A recheck of the details was asked for."}</small>
      </div>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">How the two records compare in {label}</caption>
          <thead><tr><th>Detail</th><th>How they compare</th></tr></thead>
          <tbody>
            <tr><td>Name</td><td>{FACTOR[c.factors.name]}</td></tr>
            <tr><td>Birth date</td><td>{FACTOR[c.factors.birth_date]}</td></tr>
            <tr><td>Phone</td><td>{FACTOR[c.factors.phone]}</td></tr>
          </tbody>
        </table>
      </div>
      <div className="sub">
        {c.people.map((p, i) => (
          <div key={p.id}>Record {i === 0 ? "A" : "B"}: {p.initials || "no name"}, born {p.birth_year || "unknown"}
            {p.phone_end ? `, phone ends ${p.phone_end}` : ""}{p.at_this_clinic ? "" : " (another clinic's patient)"}</div>
        ))}
        {c.flags.length > 0 && <ul>{c.flags.map((f) => <li key={f}>{FLAG[f] ?? f}</li>)}</ul>}
      </div>
      {pending && (
        <div className="notice" role="status">
          {c.you_agreed_first
            ? "You agreed first. A different person must confirm; you cannot confirm your own decision."
            : `${c.first_reviewer || "A colleague"} agrees they are the same person. A second reviewer must confirm or overrule.`}
        </div>
      )}
      {canDecide && (isOpen(c) || c.state === "linked") && (
        <div className="form-row">
          {asking === "" ? (
            <>
              {isOpen(c) && !c.recheck_requested && (
                <button className="btn ghost" disabled={busy} onClick={() => void run(c.public_id, "request_recheck", "", "Asked for the details to be checked again.")}>
                  Ask for a recheck<span className="sr-only"> of {label}</span></button>
              )}
              {isOpen(c) && !(pending && c.you_agreed_first) && (
                <button className="btn" disabled={busy} onClick={() => setAsking("same")}>They are the same person<span className="sr-only"> ({label})</span></button>
              )}
              {isOpen(c) && (
                <button className="btn" disabled={busy} onClick={() => setAsking("not_same")}>They are different people<span className="sr-only"> ({label})</span></button>
              )}
              {c.state === "linked" && (
                <button className="btn ghost" disabled={busy} onClick={() => setAsking("split")}>Send the link back for review<span className="sr-only"> ({label})</span></button>
              )}
            </>
          ) : (
            <div className="notice" role="group" aria-label={`Confirm your decision on ${label}`}>
              {asking === "same" && (c.critical && !pending
                ? "You will be the first of two reviewers. The link is confirmed only when a different person agrees too."
                : "This records that the two records are one person. Nothing is merged.")}
              {asking === "not_same" && "This closes the case. The pair will not be proposed again unless their details change."}
              {asking === "split" && "The link goes back to review. Decide again afterwards."}
              <div className="form-row">
                <select aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>
                  {NOTES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                </select>
                <button className="btn" disabled={busy} onClick={() => {
                  const action = asking === "same" ? "confirm_same" : asking === "not_same" ? "mark_not_same" : "split";
                  setAsking("");
                  void run(c.public_id, action, reason, "Decision recorded.");
                }}>Yes, record it</button>
                <button className="btn ghost" onClick={() => setAsking("")}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}
      <div className="form-row">
        <button className="btn ghost" aria-expanded={history} onClick={() => setHistory((h) => !h)}>
          {history ? "Hide history" : "Show history"}<span className="sr-only"> of {label}</span></button>
      </div>
      {history && (
        <div className="sub">
          <b>{c.chain_ok ? "History checks out" : "History does not check out"}</b>
          <ol>{c.history.map((h) => (
            <li key={h.seq}>{when(h.occurred_at)}: {ACTION[h.action] ?? h.action} by {h.by}{h.note ? ` (${NOTES.find(([v]) => v === h.note)?.[1] ?? h.note})` : ""}</li>
          ))}</ol>
        </div>
      )}
    </div>
  );
}
