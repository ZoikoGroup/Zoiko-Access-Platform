import { useState } from "react";
import { advanceAiPermit, getAiPermits, proposeAiPermit, signAiPermit, type AiPermit } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill } from "../ui";

// What each state may move to next (the documents' path; anything else is refused by the server too).
const NEXT: Record<string, string[]> = {
  PROPOSED: ["QUALIFICATION"], QUALIFICATION: ["DESIGN_CONTROL"], DESIGN_CONTROL: ["VALIDATION"],
  VALIDATION: ["SANDBOX"], SANDBOX: ["SHADOW"], SHADOW: ["RELEASE_REVIEW"],
  RELEASE_REVIEW: ["AUTHORIZED"], AUTHORIZED: ["SUSPENDED", "CHANGE_REVIEW"],
  SUSPENDED: ["CORRECTIVE_ACTION"], CORRECTIVE_ACTION: ["REVALIDATION"],
  REVALIDATION: ["RELEASE_REVIEW"], CHANGE_REVIEW: ["VALIDATION", "AUTHORIZED"], RETIRED: [],
};
const WORDS: Record<string, string> = {
  PROPOSED: "Proposed", QUALIFICATION: "Qualification", DESIGN_CONTROL: "Design control",
  VALIDATION: "Validation", SANDBOX: "Sandbox", SHADOW: "Shadow (acts on nothing)",
  RELEASE_REVIEW: "Release review", AUTHORIZED: "Authorized", SUSPENDED: "Suspended",
  CHANGE_REVIEW: "Change review", CORRECTIVE_ACTION: "Corrective action",
  REVALIDATION: "Revalidation", RETIRED: "Retired",
};
const NEEDS_REASON = new Set(["SUSPENDED", "CHANGE_REVIEW", "RETIRED"]);
const LANES: [string, string][] = [
  ["A", "A: admin help"], ["B", "B: clinical content help"], ["C", "C: decision-informing"],
  ["D", "D: device-controlled"], ["E", "E: acts on its own"],
];
const LEVELS: [string, string][] = [
  ["A0", "A0: fixed rules"], ["A1", "A1: non-clinical language"], ["A2", "A2: clinical documentation"],
  ["A3", "A3: clinical decision / device"],
];
const kind = (s: string) => (s === "AUTHORIZED" ? "ok" : s === "SUSPENDED" || s === "RETIRED" ? "bad" : "info") as "ok" | "bad" | "info";

export function AiPermits() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getAiPermits, 15_000, version);
  return <Gate result={result}>{(d) => <View me={d.me} rows={d.permits} onChanged={() => setVersion((v) => v + 1)} />}</Gate>;
}

function View({ me, rows, onChanged }: { me: string; rows: AiPermit[]; onChanged: () => void }) {
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState({ feature_id: "", purpose: "", lane: "A", level: "A1", model: "", prompt: "", tools: "", clinical_owner: "" });
  const [why, setWhy] = useState<Record<string, string>>({});
  const [days, setDays] = useState<Record<string, string>>({});

  async function run(key: string, work: () => Promise<unknown>, success: string) {
    setBusy(key); setError(""); setNote("");
    try { await work(); setNote(success); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  return (
    <>
      <div className="hero"><div>
        <h1>AI permits</h1>
        <p>AI is switched off. A feature can run only while it holds an authorized permit, and a permit needs two
          other people to sign it off first. Nothing here turns anything on by itself, and no patient text is ever
          stored: only fingerprints. Only administrators see this page.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
      <div className="grid">
        <Card title="Propose a permit" sub="This only writes the proposal down. It starts as Proposed and cannot run.">
          <div className="form-row" style={{ flexWrap: "wrap", gap: 8 }}>
            <input aria-label="Feature id" placeholder="Feature id, e.g. message.reply_draft" value={form.feature_id} onChange={set("feature_id")} />
            <input aria-label="Purpose" placeholder="What it is for (5 to 200 characters)" value={form.purpose} onChange={set("purpose")} />
            <select aria-label="Lane" value={form.lane} onChange={set("lane")}>{LANES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
            <select aria-label="Level" value={form.level} onChange={set("level")}>{LEVELS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
            <input aria-label="Model name and version" placeholder="Model name and version" value={form.model} onChange={set("model")} />
            <input aria-label="Prompt" placeholder="The exact prompt text (kept only as a fingerprint)" value={form.prompt} onChange={set("prompt")} />
            <input aria-label="Tools it may use" placeholder="Tools it may use (names, separated by spaces)" value={form.tools} onChange={set("tools")} />
            <input aria-label="Clinical owner" placeholder="Clinical owner (needed for lane B)" value={form.clinical_owner} onChange={set("clinical_owner")} />
            <button className="btn" disabled={busy === "new" || !form.feature_id || !form.purpose || !form.model || !form.prompt}
              onClick={() => void run("new", () => proposeAiPermit(form), "Permit proposed. It is not switched on.")}>Propose</button>
          </div>
        </Card>
        <Card title={`Permits (${rows.length})`}>
          {rows.length === 0 ? <div className="empty">No AI permits. Nothing can run.</div> : rows.map((p) => (
            <div key={p.id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <div className="info">
                <b>{p.feature_id} <Pill kind={kind(p.state)}>{WORDS[p.state] ?? p.state}</Pill></b>
                <small>{p.purpose} - lane {p.lane}, level {p.level}</small>
                {p.allowed_tools.length > 0 && <small>May use: {p.allowed_tools.join(", ")}</small>}
                {p.expiry && <small>Valid until {new Date(p.expiry).toLocaleDateString()}</small>}
                {p.steps.length > 0 && <small>History: {p.steps.map((s) => `${WORDS[s.to] ?? s.to}${s.reason ? ` (${s.reason})` : ""}`).join(" > ")}</small>}
              </div>
              {(p.state === "RELEASE_REVIEW" || p.state === "CHANGE_REVIEW") && (
                // CHANGE_REVIEW: sign and authorize = the change is approved as NOT material; "Move to Validation" = material.
                <div className="form-row">
                  <span>Signed by {p.signers.length} of 2 needed.</span>
                  <button className="btn ghost" disabled={busy === p.id || p.created_by === me || p.signers.includes(me)}
                    onClick={() => void run(p.id, () => signAiPermit(p.id), "Signed.")}>
                    {p.created_by === me ? "You proposed it: someone else signs" : p.signers.includes(me) ? "You signed" : "Sign off"}
                  </button>
                  <input aria-label={`Days valid for ${p.feature_id}`} placeholder="Days valid (1-365)" value={days[p.id] ?? ""}
                    onChange={(e) => setDays({ ...days, [p.id]: e.target.value })} style={{ width: 150 }} />
                  <button className="btn" disabled={busy === p.id || p.signers.length < 2 || !days[p.id]}
                    onClick={() => void run(p.id, () => advanceAiPermit(p.id, "AUTHORIZED", "", Number(days[p.id])), "Authorized.")}>Authorize</button>
                </div>
              )}
              {(NEXT[p.state] ?? []).filter((t) => t !== "AUTHORIZED").map((to) => (
                <div key={to} className="form-row">
                  {NEEDS_REASON.has(to) && (
                    <input aria-label={`Reason to move ${p.feature_id} to ${WORDS[to]}`} placeholder="Reason (required)" value={why[p.id] ?? ""}
                      onChange={(e) => setWhy({ ...why, [p.id]: e.target.value })} />
                  )}
                  <button className="btn ghost" disabled={busy === p.id || (NEEDS_REASON.has(to) && !(why[p.id] ?? "").trim())}
                    onClick={() => void run(p.id, () => advanceAiPermit(p.id, to, why[p.id] ?? ""), `Moved to ${WORDS[to]}.`)}>
                    Move to {WORDS[to]}
                  </button>
                </div>
              ))}
              {p.state !== "RETIRED" && (
                <div className="form-row">
                  <input aria-label={`Reason to retire ${p.feature_id}`} placeholder="Reason to retire (required)" value={why[`r${p.id}`] ?? ""}
                    onChange={(e) => setWhy({ ...why, [`r${p.id}`]: e.target.value })} />
                  <button className="btn ghost" disabled={busy === p.id || !(why[`r${p.id}`] ?? "").trim()}
                    onClick={() => void run(p.id, () => advanceAiPermit(p.id, "RETIRED", why[`r${p.id}`]), "Retired for good.")}>Retire for good</button>
                </div>
              )}
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
