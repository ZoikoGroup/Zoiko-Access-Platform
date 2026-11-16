import { useState } from "react";
import { getProgram, programStep } from "../api";
import type { Shown } from "../types";
import { Card, StatCard } from "../ui";
import { Gate, useLoad } from "../useLoad";

function count(s: Shown, k: number): { value: string; note?: string } {
  return s.hidden ? { value: "Hidden", note: `Fewer than ${k} people: hidden so no one can be picked out` } : { value: String(s.value) };
}

function Editor({ onDone }: { onDone: (said?: string) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refs = text.split(/[\s,;]+/).filter(Boolean);

  async function run(step: "enrol" | "withdraw") {
    setBusy(true); setError("");
    try {
      const r = await programStep(step, refs);
      setText("");
      onDone(step === "enrol" ? `${r.added ?? 0} enrolled, ${r.already_enrolled ?? 0} already enrolled.` : `${r.withdrawn ?? 0} withdrawn.`);
    } catch (err) {
      setError((err as { code?: string }).code === "VALIDATION_FAILED"
        ? "Each reference must be 3 to 40 letters, digits or . _ : - and you can send up to 200 at a time."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }

  return (
    <div className="ref-form">
      <label>References, one per line or separated by commas
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="emp-0001, emp-0002" />
      </label>
      <small>Use your own staff numbers, never names or email addresses. They are not shown back to you: you only see counts.</small>
      {error && <div className="error" role="alert">{error}</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn" disabled={busy || refs.length === 0} onClick={() => void run("enrol")}>Enrol {refs.length || ""}</button>
        <button className="btn ghost" disabled={busy || refs.length === 0} onClick={() => void run("withdraw")}>Withdraw {refs.length || ""}</button>
      </div>
    </div>
  );
}

export function Programme() {
  const [n, setN] = useState(0);
  const [said, setSaid] = useState("");       // kept here so a reload does not erase it
  return <ProgrammeInner key={n} said={said} onChanged={(m) => { setSaid(m ?? ""); setN(n + 1); }} />;
}

function ProgrammeInner({ onChanged, said }: { onChanged: (said?: string) => void; said: string }) {
  const summary = useLoad(getProgram, 60_000);
  return (
    <>
      <div className="hero"><div>
        <h1>Programme</h1>
        <p>Counts for the people you have enrolled. You never see an individual here, and a group smaller than the minimum is hidden.</p>
      </div></div>
      <Gate result={summary}>
        {(s) => (
          <div className="grid">
            <StatCard icon="patients" label="Enrolled" {...count(s.enrolled, s.min_group)} />
            <StatCard icon="referrals" label="Withdrawn" {...count(s.withdrawn, s.min_group)} />
            <Card title="Enrol or withdraw people">
              <Editor onDone={onChanged} />
              {said && <div className="success" role="status">{said}</div>}
            </Card>
          </div>
        )}
      </Gate>
    </>
  );
}
