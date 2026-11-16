import { useCallback, useEffect, useState } from "react";
import { ApiError, createPack, getPacks, packStep, type PackManifest, type PackRow } from "../api";
import { Card, Pill } from "../ui";

type PillKind = "ok" | "warn" | "bad" | "info";
const STATUS: Record<PackRow["status"], { text: string; kind: PillKind }> = {
  draft: { text: "Draft", kind: "info" }, validated: { text: "Checked", kind: "info" },
  expert_review: { text: "With the reviewers", kind: "warn" },
  signed: { text: "Approved on record", kind: "ok" },
  suspended: { text: "Suspended", kind: "bad" }, retired: { text: "Retired", kind: "info" },
};
const CAPABILITIES: [string, string][] = [
  ["provider_directory", "Find a doctor"], ["booking", "Booking"], ["messaging", "Messages"],
  ["referrals", "Referrals"], ["waiting_list", "Waiting list"],
];
const EVIDENCE: [string, string][] = [["legal", "Legal"], ["dpia", "Privacy assessment"], ["cyber", "Security review"], ["clinical", "Clinical"]];
const EVIDENCE_WORDS: Record<string, string> = { PENDING: "Not yet", PROVIDED: "Provided", NOT_APPLICABLE_REVIEW_NEEDED: "Not applicable (review needed)" };

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "CONFLICT_VERSION" ? "That cannot be done in the pack's present state, or the name is already used."
    : code === "VALIDATION_FAILED" ? "Something is missing or not allowed. Check the fields and the list of what is missing."
    : code === "POLICY_DENIED" ? "Your account may not do that step (nobody approves their own work, and each approval needs the right kind of staff)."
    : e instanceof Error ? e.message : "That did not work";
}

const BLANK: PackManifest = {
  settings: { data_request_days: null, emergency_number: "", default_language: "", time_zone: "" },
  capabilities: {}, evidence: { legal: "PENDING", dpia: "PENDING", cyber: "PENDING", clinical: "PENDING" },
  validity: { expires_on: null },
};

function ManifestFields({ m, onChange }: { m: PackManifest; onChange: (m: PackManifest) => void }) {
  const set = (s: Partial<PackManifest["settings"]>) => onChange({ ...m, settings: { ...m.settings, ...s } });
  return (
    <>
      <label>Days allowed to answer a data request
        <input type="number" min={1} max={365} value={m.settings.data_request_days ?? ""} onChange={(e) => set({ data_request_days: e.target.value ? Number(e.target.value) : null })} />
      </label>
      <label>Emergency number<input value={m.settings.emergency_number} onChange={(e) => set({ emergency_number: e.target.value })} placeholder="112" /></label>
      <label>Default language<input value={m.settings.default_language} onChange={(e) => set({ default_language: e.target.value })} placeholder="en-GB" /></label>
      <label>Time zone<input value={m.settings.time_zone} onChange={(e) => set({ time_zone: e.target.value })} placeholder="Europe/London" /></label>
      <fieldset><legend>What the pack switches on</legend>
        {CAPABILITIES.map(([code, text]) => (
          <label key={code} className="check">
            <input type="checkbox" checked={!!m.capabilities[code]} onChange={(e) => onChange({ ...m, capabilities: { ...m.capabilities, [code]: e.target.checked } })} /> {text}
          </label>
        ))}
        <small>Diagnostics, clinical AI and record exchange are not built, so they cannot be switched on.</small>
      </fieldset>
      <fieldset><legend>Evidence (the documents themselves stay in the country's own vault)</legend>
        {EVIDENCE.map(([code, text]) => (
          <label key={code}>{text}
            <select value={m.evidence[code] ?? "PENDING"} onChange={(e) => onChange({ ...m, evidence: { ...m.evidence, [code]: e.target.value } })}>
              {Object.entries(EVIDENCE_WORDS).map(([v, w]) => <option key={v} value={v}>{w}</option>)}
            </select>
          </label>
        ))}
      </fieldset>
      <label>Review again by (required)
        <input type="date" value={m.validity.expires_on ?? ""} onChange={(e) => onChange({ ...m, validity: { expires_on: e.target.value || null } })} />
      </label>
    </>
  );
}

function PackCard({ p, canWrite, canAttest, onDone }: { p: PackRow; canWrite: boolean; canAttest: boolean; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState<PackManifest | null>(null);
  async function run(step: string, body: Record<string, unknown> = {}) {
    setBusy(true); setError("");
    try { await packStep(p.id, step, body); setNote(""); setDraft(null); await onDone(); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }
  const reviewer = canWrite || canAttest;
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{p.country} · {p.pack_ref} (revision {p.revision}) <Pill kind={STATUS[p.status].kind}>{STATUS[p.status].text}</Pill>{p.expired && <> <Pill kind="bad">Review date passed</Pill></>}</b>
        <small>Written by {p.author}. Fingerprint {p.content_hash.slice(0, 12)}.</small>
        <small>Data requests: {p.manifest.settings.data_request_days ? `${p.manifest.settings.data_request_days} days` : "no deadline set"}. Emergency number: {p.manifest.settings.emergency_number || "not set"}. Review by {p.manifest.validity.expires_on ?? "not set"}.</small>
        {p.missing.length > 0 && <small>Still missing: {p.missing.join(", ")}.</small>}
        {Object.keys(p.approvals).length > 0 && <small>Approved by: {Object.entries(p.approvals).map(([r, a]) => `${r} (${a.by})`).join(", ")}.</small>}
        {p.note && <small>Last note: {p.note}</small>}
        {error && <small style={{ color: "var(--bad)" }} role="alert">{error}</small>}
      </div>
      {draft && (
        <form className="add-form" onSubmit={(e) => { e.preventDefault(); void run("edit", { manifest: draft }); }}>
          <ManifestFields m={draft} onChange={setDraft} />
          <div><button className="btn" type="submit" disabled={busy}>Save changes</button> <button className="btn ghost" type="button" onClick={() => setDraft(null)}>Cancel</button></div>
        </form>
      )}
      <div className="form-row">
        {canWrite && p.status === "draft" && !draft && <button className="btn ghost" disabled={busy} onClick={() => setDraft(p.manifest)}>Edit</button>}
        {canWrite && p.status === "draft" && <button className="btn ghost" disabled={busy || p.missing.length > 0} onClick={() => void run("validate")}>Mark as checked</button>}
        {canWrite && p.status === "validated" && <button className="btn ghost" disabled={busy} onClick={() => void run("submit")}>Send to the reviewers</button>}
        {canWrite && p.status === "expert_review" && !p.approvals.legal && <button className="btn" disabled={busy} onClick={() => void run("approve-legal")}>Approve (legal)</button>}
        {canAttest && p.status === "expert_review" && !p.approvals.clinical && <button className="btn" disabled={busy} onClick={() => void run("approve-clinical")}>Approve (clinical safety)</button>}
        {reviewer && ["validated", "expert_review", "signed"].includes(p.status) && (
          <>
            <input aria-label={`Reason for ${p.pack_ref}`} placeholder="Reason (needed to send back or suspend)" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
            {p.status !== "signed" && <button className="btn ghost" disabled={busy || note.trim().length < 3} onClick={() => void run("reject", { note })}>Send back</button>}
            {p.status === "signed" && <button className="btn ghost" disabled={busy || note.trim().length < 3} onClick={() => void run("suspend", { note })}>Suspend</button>}
          </>
        )}
        {canWrite && p.status === "suspended" && <button className="btn ghost" disabled={busy} onClick={() => void run("revalidate")}>Start a new revision</button>}
        {canWrite && (p.status === "signed" || p.status === "suspended") && <button className="btn ghost" disabled={busy} onClick={() => void run("retire")}>Retire</button>}
      </div>
    </div>
  );
}

/** Country packs: the recorded rules per country, how far they have been reviewed. Never makes one live. */
export function Packs({ canWrite, canAttest }: { canWrite: boolean; canAttest: boolean }) {
  const [rows, setRows] = useState<PackRow[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ pack_ref: "", country: "", service: "care_navigation" });
  const [manifest, setManifest] = useState<PackManifest>(BLANK);
  const load = useCallback(async () => {
    try { setRows(await getPacks()); setError(""); } catch (e) { setError(friendly(e)); setRows([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await createPack({ ...form, country: form.country.toUpperCase(), manifest }); setForm({ ...form, pack_ref: "" }); setManifest(BLANK); await load(); }
    catch (err) { setError(friendly(err)); }
    finally { setBusy(false); }
  }
  return (
    <>
      <div className="hero"><div>
        <h1>Country packs</h1>
        <p>The recorded rules for one country: data-request deadline, emergency number, language, what is switched on, and what evidence exists. A pack moves from draft to "approved on record" only when it is complete and two different people (support and clinical safety, neither the author) approve the exact content. "Approved on record" is not a licence and does not make anything live: going live needs steps that are not built yet.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        {canWrite && (
          <Card title="Start a pack" sub="Needs support rights. It begins as a draft that nobody has reviewed.">
            <form className="add-form" onSubmit={(e) => void add(e)}>
              <label>Name (lowercase, with dashes)<input value={form.pack_ref} maxLength={64} onChange={(e) => setForm({ ...form, pack_ref: e.target.value })} placeholder="gb-care-navigation" required /></label>
              <label>Country code (2 letters)<input value={form.country} maxLength={2} onChange={(e) => setForm({ ...form, country: e.target.value })} placeholder="GB" required /></label>
              <ManifestFields m={manifest} onChange={setManifest} />
              <div><button className="btn" type="submit" disabled={busy}>{busy ? "Adding…" : "Start the pack"}</button></div>
            </form>
          </Card>
        )}
        <Card title={`${rows?.length ?? 0} pack${rows?.length === 1 ? "" : "s"}`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0 ? <div className="empty">No country pack yet.</div>
            : rows.map((p) => <PackCard key={p.id} p={p} canWrite={canWrite} canAttest={canAttest} onDone={load} />)}
        </Card>
      </div>
    </>
  );
}
