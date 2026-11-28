import { useCallback, useEffect, useState } from "react";
import { ApiError, createRelease, getNamedPeople, getOnCallStaff, getReleasesReadiness, namePerson, namedStep, overrideBudget, releaseStep, type NamedPeople, type OnCallPerson, type ReleaseReadiness } from "../api";
import { Card, Pill } from "../ui";

type PillKind = "ok" | "warn" | "bad" | "info";
const DECISION: Record<string, { text: string; kind: PillKind }> = {
  no_go: { text: "Not ready", kind: "bad" }, g3_ready: { text: "Ready for production (G3)", kind: "warn" },
  g4_authorized: { text: "Authorised for the country (G4)", kind: "ok" },
};
const STATUS: Record<string, { text: string; kind: PillKind }> = {
  signed: { text: "Signed", kind: "ok" }, missing: { text: "Missing", kind: "info" },
  expired: { text: "Expired", kind: "warn" }, vetoed: { text: "Vetoed", kind: "bad" },
};
const WORDS: Record<string, string> = {
  signed_build: "Signed build and supply chain", security_signoff: "Security sign-off",
  privacy_signoff: "Privacy sign-off", requirements_coverage: "Requirements covered by tests",
  recovery_exercised: "Recovery exercised", operations_staffed: "Operations staffed",
  legal_authority: "Legal authority for the country", clinical_partner: "Clinical partner in place",
  clinical_safety_signoff: "Clinical safety sign-off", operating_entity: "Operating entity",
  country_pack: "Signed country pack (checked by the system)",
  hazard_register: "Every hazard accepted or retired (checked by the system)",
  open_incidents: "No serious or unassessed incident open (checked by the system)",
  intended_purpose: "Every capability has an approved intended purpose (checked by the system)",
  clinical_safety_matrix: "No clinical-safety requirement is marked not built (checked by the system)",
  error_budget: "The 30-day error budget is not used up (checked by the system)",
  zoiko_two_step: "Zoiko staff must use two-step sign-in (checked by the system)",
  cto_signoff: "Signed by the CTO", clinical_lead_signoff: "Signed by the Clinical safety lead",
  key_recovery: "Two people hold a sealed copy of the master key (checked by the system)",
};
const TITLES: Record<string, string> = {
  key_recovery: "Holds a sealed copy of the master key", cto: "CTO (signs releases)",
  clinical_safety_lead: "Clinical safety lead (signs releases)",
};

/** Who holds the master key's recovery copies and who signs releases (decided 27 Nov 2026). */
function NamedPeopleCard() {
  const [data, setData] = useState<NamedPeople | null>(null);
  const [staff, setStaff] = useState<OnCallPerson[]>([]);
  const [form, setForm] = useState({ title: "key_recovery", holder: "", envelope: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setData(await getNamedPeople()); setError(""); } catch (e) { setError(friendly(e)); }
  }, []);
  useEffect(() => { void load(); getOnCallStaff().then(setStaff).catch(() => setStaff([])); }, [load]);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await load(); } catch (e) { setError(friendly(e)); } finally { setBusy(false); }
  }
  if (!data) return null;
  const live = data.named.filter((n) => n.status !== "withdrawn");
  return (
    <Card title="Named people" sub={`Master key recovery copies confirmed: ${data.key_holders_confirmed} of ${data.key_holders_needed} needed. Every production release is signed by the CTO and the Clinical safety lead. A name counts only after that person confirms it.`}>
      {error && <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>}
      {live.length === 0 && <div className="empty">Nobody has been named yet.</div>}
      {live.map((n) => (
        <div key={n.public_id} className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <div className="info">
            <b>{n.holder_name}: {TITLES[n.title]} <Pill kind={n.status === "confirmed" ? "ok" : "warn"}>{n.status === "confirmed" ? "Confirmed" : "Waiting for them to confirm"}</Pill></b>
            <small>Named by {n.appointed_by_name}.{n.title === "key_recovery" ? ` Key ${n.key_id}, sealed at ${n.envelope_code}.` : ""}</small>
          </div>
          <div className="form-row">
            {n.holder === data.me && n.status !== "confirmed" && (
              <button className="btn" disabled={busy} onClick={() => void run(() => namedStep(n.public_id, "confirm"))}>
                {n.title === "key_recovery" ? "I hold the sealed copy" : "I accept"}
              </button>
            )}
            {(data.can_name || n.holder === data.me) && (
              <button className="btn ghost" disabled={busy} onClick={() => void run(() => namedStep(n.public_id, "withdraw"))}>Withdraw</button>
            )}
          </div>
        </div>
      ))}
      {data.can_name && (
        <form className="add-form" onSubmit={(e) => { e.preventDefault(); void run(() => namePerson(form.title, form.holder, form.envelope)); }}>
          <label>Responsibility<select value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}>
            {Object.entries(TITLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
          <label>Person<select value={form.holder} onChange={(e) => setForm({ ...form, holder: e.target.value })} required>
            <option value="">Choose…</option>
            {staff.filter((p) => p.id !== data.me).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select></label>
          {form.title === "key_recovery" && (
            <label>Where it is sealed (a short code, never the key)<input value={form.envelope} maxLength={40} placeholder="safe-a-1" onChange={(e) => setForm({ ...form, envelope: e.target.value })} required /></label>
          )}
          <div><button className="btn" type="submit" disabled={busy || !form.holder}>Name them</button></div>
        </form>
      )}
    </Card>
  );
}
const CAPABILITIES = ["provider_directory", "booking", "messaging", "referrals", "waiting_list"];

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "Your account may not do that. Each requirement needs the right kind of officer, never the person who made the release, and nobody signs two."
    : code === "CONFLICT_VERSION" ? "That cannot be done: the name is taken, or the requirement has been vetoed (a veto stands: make a new release)."
    : code === "VALIDATION_FAILED" ? "Check the fields: a real artefact id and an expiry date within the next year."
    : e instanceof Error ? e.message : "That did not work";
}

function Requirement({ rel, q, canSign, canVeto, onDone }: {
  rel: ReleaseReadiness; q: ReleaseReadiness["requirements"][number]; canSign: boolean; canVeto: boolean; onDone: () => Promise<void>;
}) {
  const [ref, setRef] = useState("");
  const [expires, setExpires] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const system = q.requirement === "country_pack" || q.requirement === "hazard_register" || q.requirement === "open_incidents" || q.requirement === "intended_purpose" || q.requirement === "clinical_safety_matrix" || q.requirement === "error_budget";
  async function run(step: "sign" | "veto") {
    setBusy(true); setError("");
    try {
      await releaseStep(rel.public_id, step, step === "sign"
        ? { requirement: q.requirement, reference: ref, expires_on: expires, note }
        : { requirement: q.requirement, note });
      setRef(""); setNote(""); await onDone();
    } catch (e) { setError(friendly(e)); } finally { setBusy(false); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{q.gate} · {WORDS[q.requirement] ?? q.requirement} <Pill kind={STATUS[q.status].kind}>{STATUS[q.status].text}</Pill></b>
        {q.status === "signed" && <small>{system ? `Covered by ${q.reference}.` : `Signed by ${q.signer} (${q.role}), artefact ${q.reference}, valid to ${q.expires_on}.`}</small>}
        {q.status === "expired" && <small>Expired {q.expires_on}: it no longer counts.</small>}
        {q.status === "vetoed" && <small>Vetoed. {q.note}</small>}
        {q.requirement === "error_budget" && q.status === "signed" && q.signer && <small>Overridden by clinical safety until {q.expires_on}: {q.note}</small>}
        {q.requirement === "error_budget" && q.status === "missing" && <small>{q.reference}. Releases wait (runbook 08).</small>}
        {error && <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>}
      </div>
      {q.requirement === "error_budget" && q.status === "missing" && canSign && <BudgetOverride rel={rel} onDone={onDone} />}
      {!system && q.status !== "vetoed" && (canSign || canVeto) && (
        <div className="form-row">
          {canSign && q.status !== "signed" && (
            <>
              <input aria-label={`Artefact id for ${WORDS[q.requirement]}`} placeholder="Artefact id" value={ref} maxLength={64} onChange={(e) => setRef(e.target.value)} />
              <input aria-label={`Valid until for ${WORDS[q.requirement]}`} type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
              <button className="btn" disabled={busy || !ref || !expires} onClick={() => void run("sign")}>Sign</button>
            </>
          )}
          {canVeto && (
            <>
              <input aria-label={`Reason to veto ${WORDS[q.requirement]}`} placeholder="Reason (to veto)" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
              <button className="btn ghost" disabled={busy || note.trim().length < 3} onClick={() => void run("veto")}>Veto</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** A safety officer lets ONE release go on while the error budget is used up: a fix for the cause,
 *  with a reason, for at most 7 days (DEV-388, GHE-18). */
function BudgetOverride({ rel, onDone }: { rel: ReleaseReadiness; onDone: () => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [days, setDays] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function go() {
    setBusy(true); setError("");
    try { await overrideBudget(rel.public_id, reason.trim(), days); setReason(""); await onDone(); }
    catch (e) { setError(friendly(e)); } finally { setBusy(false); }
  }
  return (
    <div className="form-row">
      <input aria-label="Why this release must go ahead" placeholder="Why (a fix for the cause, never a feature)" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
      <select aria-label="For how many days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {[1, 2, 3, 4, 5, 6, 7].map((d) => <option key={d} value={d}>{d} day{d === 1 ? "" : "s"}</option>)}
      </select>
      <button className="btn" disabled={busy || reason.trim().length < 10} onClick={() => void go()}>Override the error-budget hold</button>
      {error && <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>}
    </div>
  );
}

function ReleaseCard({ r, canWrite, canAttest, onDone }: { r: ReleaseReadiness; canWrite: boolean; canAttest: boolean; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const clinical = (name: string) => name === "clinical_partner" || name === "clinical_safety_signoff";
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{r.release_ref} · {r.country} <Pill kind={DECISION[r.decision].kind}>{DECISION[r.decision].text}</Pill></b>
        <small>Build {r.build}, cell {r.cell}, capabilities: {r.capabilities.join(", ")}. Made by {r.author}. Fingerprint {r.tuple_digest.slice(0, 12)}.</small>
        {r.blockers.length > 0 && <small>{r.blockers.length} thing{r.blockers.length === 1 ? "" : "s"} still stand in the way.</small>}
        {r.recorded_decision !== r.decision && <small>The recorded decision is "{DECISION[r.recorded_decision].text}"; it will be updated when someone presses "Record the decision now".</small>}
      </div>
      <div className="form-row">
        <button className="btn ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide the requirements" : "Show the requirements"}</button>
        {(canWrite || canAttest) && <button className="btn ghost" onClick={() => void releaseStep(r.public_id, "evaluate", {}).then(onDone)}>Record the decision now</button>}
      </div>
      {open && r.requirements.map((q) => (
        <Requirement key={q.requirement} rel={r} q={q} onDone={onDone}
          canSign={clinical(q.requirement) ? canAttest : canWrite} canVeto={canWrite || canAttest} />
      ))}
    </div>
  );
}

/** Go-live readiness: what must be signed before a release may go live in a country. Records only. */
export function Readiness({ canWrite, canAttest }: { canWrite: boolean; canAttest: boolean }) {
  const [rows, setRows] = useState<ReleaseReadiness[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ release_ref: "", build: "", cell: "", country: "" });
  const [caps, setCaps] = useState<string[]>(["booking"]);
  const load = useCallback(async () => {
    try { setRows(await getReleasesReadiness()); setError(""); } catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await createRelease({ ...form, country: form.country.toUpperCase(), capabilities: caps }); setForm({ ...form, release_ref: "" }); await load(); }
    catch (err) { setError(friendly(err)); } finally { setBusy(false); }
  }
  return (
    <>
      <div className="hero"><div>
        <h1>Go-live readiness</h1>
        <p>For one release in one country: eight production requirements (G3, including the CTO's and the Clinical safety lead's signatures) and four country requirements (G4), each signed for that exact release by a different person, plus a signed country pack that switches on everything the release uses. Any support or safety officer can veto; a veto cannot be undone. An approval stops counting when it expires. This page only records and works out a decision: it never deploys or switches anything on, and "authorised" is not a licence.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        {canWrite && (
          <Card title="Start a release" sub="A release is fixed once made: a different build, country or set of capabilities is a different release.">
            <form className="add-form" onSubmit={(e) => void add(e)}>
              <label>Name<input value={form.release_ref} maxLength={64} onChange={(e) => setForm({ ...form, release_ref: e.target.value })} placeholder="rel-2026-11-1" required /></label>
              <label>Build<input value={form.build} maxLength={80} onChange={(e) => setForm({ ...form, build: e.target.value })} placeholder="build-2026.11.1" required /></label>
              <label>Cell<input value={form.cell} maxLength={32} onChange={(e) => setForm({ ...form, cell: e.target.value })} placeholder="DEV-01" required /></label>
              <label>Country (2 letters)<input value={form.country} maxLength={2} onChange={(e) => setForm({ ...form, country: e.target.value })} placeholder="GB" required /></label>
              <fieldset><legend>Capabilities this release uses</legend>
                {CAPABILITIES.map((c) => (
                  <label key={c} className="check"><input type="checkbox" checked={caps.includes(c)} onChange={(e) => setCaps(e.target.checked ? [...caps, c] : caps.filter((x) => x !== c))} /> {c.replace("_", " ")}</label>
                ))}
              </fieldset>
              <div><button className="btn" type="submit" disabled={busy || caps.length === 0}>Start the release</button></div>
            </form>
          </Card>
        )}
        <NamedPeopleCard />
        <Card title={`${rows?.length ?? 0} release${rows?.length === 1 ? "" : "s"}`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">No release yet.</div>
            : rows.map((r) => <ReleaseCard key={r.public_id} r={r} canWrite={canWrite} canAttest={canAttest} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
