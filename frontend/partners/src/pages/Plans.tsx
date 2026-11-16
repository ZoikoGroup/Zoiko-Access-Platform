import { useState } from "react";
import { addPlan, addPlanMember, changePlan, checkEntitlement, endPlanMember, getPlanMembers, getPlans } from "../api";
import type { Entitlement, Plan } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const WHY: Record<string, string> = {
  covered: "Covered", no_member: "No member with that number", member_ended: "The membership has ended",
  outside_dates: "Outside the membership dates", plan_inactive: "The plan is switched off",
  not_covered: "This kind of service is not in the plan",
};

function problem(err: unknown, fallback: string): string {
  const code = (err as { code?: string }).code;
  return code === "CONFLICT_VERSION" ? "That already exists."
    : code === "VALIDATION_FAILED" ? "Check the fields: codes are capitals, categories are lower-case words, the share is 0 to 100."
    : err instanceof Error ? err.message : fallback;
}

function AddPlan({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [cats, setCats] = useState("general_physician");
  const [share, setShare] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await addPlan({ code, name, covered_categories: cats.split(/[\s,;]+/).filter(Boolean), copay_percent: Number(share) });
      setCode(""); setName(""); onDone();
    } catch (err) { setError(problem(err, "Could not add the plan")); }
    finally { setBusy(false); }
  }
  return (
    <form className="ref-form" onSubmit={(e) => void submit(e)}>
      <label>Plan code (capitals)<input className="field" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} required maxLength={20} /></label>
      <label>Plan name<input className="field" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} /></label>
      <label>Kinds of service it covers (categories, separated by commas)<input className="field" value={cats} onChange={(e) => setCats(e.target.value)} required /></label>
      <label>The patient pays (percent)<input className="field" type="number" min={0} max={100} value={share} onChange={(e) => setShare(e.target.value)} required /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || !code || !name}>{busy ? "Adding…" : "Add plan"}</button>
    </form>
  );
}

function PlanRow({ p, onDone }: { p: Plan; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  async function toggle() {
    setBusy(true);
    try { await changePlan(p.id, { active: !p.active }); onDone(); } finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{p.code}: {p.name} <Pill kind={p.active ? "ok" : "warn"}>{p.active ? "on" : "off"}</Pill></b>
        <small>Covers {p.covered_categories.join(", ")}. The patient pays {p.copay_percent}%.</small>
      </div>
      <button className="btn ghost" disabled={busy} onClick={() => void toggle()}>{p.active ? "Switch off" : "Switch on"}</button>
    </div>
  );
}

function AddMember({ plans, onDone }: { plans: Plan[]; onDone: () => void }) {
  const [plan, setPlan] = useState(plans[0]?.code ?? "");
  const [memberId, setMemberId] = useState("");
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await addPlanMember({ plan, member_id: memberId, ...(from ? { valid_from: from } : {}), ...(until ? { valid_until: until } : {}) });
      setMemberId(""); onDone();
    } catch (err) { setError(problem(err, "Could not add the member")); }
    finally { setBusy(false); }
  }
  if (plans.length === 0) return <div className="empty">Add a plan first.</div>;
  return (
    <form className="ref-form" onSubmit={(e) => void submit(e)}>
      <label>Plan<select value={plan} onChange={(e) => setPlan(e.target.value)}>{plans.map((p) => <option key={p.id} value={p.code}>{p.code}</option>)}</select></label>
      <label>Member number<input className="field" value={memberId} onChange={(e) => setMemberId(e.target.value)} required /></label>
      <label>Covered from (optional)<input className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
      <label>Covered until (optional)<input className="field" type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || !memberId}>{busy ? "Adding…" : "Add member"}</button>
    </form>
  );
}

function Check() {
  const [memberId, setMemberId] = useState("");
  const [category, setCategory] = useState("general_physician");
  const [on, setOn] = useState("");
  const [answer, setAnswer] = useState<Entitlement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setAnswer(null);
    try { setAnswer(await checkEntitlement(memberId, category, on || undefined)); }
    catch (err) { setError(problem(err, "Could not check")); }
    finally { setBusy(false); }
  }
  return (
    <form className="ref-form" onSubmit={(e) => void submit(e)}>
      <label>Member number<input className="field" value={memberId} onChange={(e) => setMemberId(e.target.value)} required /></label>
      <label>Kind of service<input className="field" value={category} onChange={(e) => setCategory(e.target.value)} required /></label>
      <label>On this day (optional)<input className="field" type="date" value={on} onChange={(e) => setOn(e.target.value)} /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || !memberId || !category}>{busy ? "Checking…" : "Check cover"}</button>
      {answer && (
        <div className={answer.covered ? "success" : "notice"} role="status" data-testid="entitlement-answer">
          {WHY[answer.reason] ?? answer.reason}
          {answer.covered && ` under ${answer.plan}: the patient pays ${answer.copay_percent}%.`}
        </div>
      )}
    </form>
  );
}

export function Plans() {
  const [n, setN] = useState(0);
  return <PlansInner key={n} onChanged={() => setN(n + 1)} />;
}

function PlansInner({ onChanged }: { onChanged: () => void }) {
  const plans = useLoad(getPlans, 60_000);
  const members = useLoad(getPlanMembers, 60_000);
  return (
    <>
      <div className="hero"><div>
        <h1>Plans and members</h1>
        <p>Your plans, the people they cover, and a check that says whether a member is covered for a kind of service and why not when they are not. Member numbers are shown shortened.</p>
      </div></div>
      <div className="grid">
        <Card title="Check cover"><Check /></Card>
        <Card title="Your plans">
          <Gate result={plans}>{(rows) => rows.length === 0 ? <div className="empty">No plans yet.</div> : rows.map((p) => <PlanRow key={p.id} p={p} onDone={onChanged} />)}</Gate>
        </Card>
        <Card title="Add a plan"><AddPlan onDone={onChanged} /></Card>
        <Card title="Members">
          <Gate result={members}>
            {(rows) => rows.length === 0 ? <div className="empty">No members yet.</div> : rows.map((m) => (
              <div className="appt" key={m.id}>
                <div className="info">
                  <b>{m.member} <Pill kind={m.state === "active" ? "ok" : "info"}>{m.state === "active" ? "enrolled" : "ended"}</Pill></b>
                  <small>Plan {m.plan}{m.valid_from ? ` from ${m.valid_from}` : ""}{m.valid_until ? ` until ${m.valid_until}` : ""}</small>
                </div>
                {m.state === "active" && <button className="btn ghost" onClick={() => void endPlanMember(m.id).then(onChanged)}>End membership</button>}
              </div>
            ))}
          </Gate>
        </Card>
        <Card title="Add a member">
          <Gate result={plans}>{(rows) => <AddMember plans={rows} onDone={onChanged} />}</Gate>
        </Card>
      </div>
    </>
  );
}
