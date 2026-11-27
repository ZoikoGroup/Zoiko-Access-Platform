import { useState } from "react";
import { Pill, type PillKind } from "./ui";

export interface Qualification {
  id: string; kind: string; kind_text: string; title: string; issuer: string; reference: string;
  obtained_on: string | null; expires_on: string | null;
  state: "recorded" | "verified" | "withdrawn" | "expired";
  checked_on: string | null; check_note: string; withdrawn_reason: string;
}
export interface Privilege {
  id: string; service: string; service_name: string; valid_from: string; valid_until: string | null;
  note: string; state: "in_force" | "not_started" | "ended" | "withdrawn"; withdrawn_reason: string;
}
export interface NewQualification {
  kind: string; title: string; issuer: string; reference: string; obtained_on: string; expires_on: string;
}

const STATE: Record<Qualification["state"], { text: string; kind: PillKind }> = {
  recorded: { text: "Not checked yet", kind: "warn" }, verified: { text: "Checked", kind: "ok" },
  expired: { text: "Expired", kind: "bad" }, withdrawn: { text: "Withdrawn", kind: "info" },
};
export const PRIVILEGE_STATE: Record<Privilege["state"], { text: string; kind: PillKind }> = {
  in_force: { text: "In force", kind: "ok" }, not_started: { text: "Not started", kind: "info" },
  ended: { text: "Ended", kind: "warn" }, withdrawn: { text: "Withdrawn", kind: "info" },
};
const KINDS: [string, string][] = [
  ["degree", "Degree or diploma"], ["registration", "Professional registration or licence"],
  ["specialty", "Specialty"], ["other", "Other"],
];
const EMPTY: NewQualification = { kind: "degree", title: "", issuer: "", reference: "", obtained_on: "", expires_on: "" };

/** One qualification as the doctor and the clinic see it, with whatever actions the screen adds. */
export function QualificationRow({ q, children }: { q: Qualification; children?: React.ReactNode }) {
  return (
    <div className="appt">
      <div className="info">
        <b>{q.title} <Pill kind={STATE[q.state].kind}>{STATE[q.state].text}</Pill></b>
        <small>{q.kind_text} · {q.issuer}{q.reference ? ` · number ${q.reference}` : ""}{q.obtained_on ? ` · from ${q.obtained_on}` : ""}{q.expires_on ? ` · until ${q.expires_on}` : ""}</small>
        {q.checked_on && <small>Checked on {q.checked_on}: {q.check_note}</small>}
        {q.withdrawn_reason && <small>Withdrawn: {q.withdrawn_reason}</small>}
      </div>
      {children}
    </div>
  );
}

/** Writing down a new qualification. */
export function QualificationForm({ onAdd }: { onAdd: (q: NewQualification) => Promise<void> }) {
  const [f, setF] = useState<NewQualification>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k: keyof NewQualification, v: string) => setF((cur) => ({ ...cur, [k]: v }));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await onAdd(f); setF(EMPTY); }
    catch (err) {
      setError((err as { code?: string }).code === "VALIDATION_FAILED"
        ? "Check the details: a name and who issued it are required, and the dates must be real, with the end after the start."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  return (
    <form className="profile-form" onSubmit={(e) => void submit(e)} style={{ marginTop: 16 }}>
      <label>Kind
        <select value={f.kind} onChange={(e) => set("kind", e.target.value)}>{KINDS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      </label>
      <label>Name of the qualification<input value={f.title} maxLength={120} onChange={(e) => set("title", e.target.value)} placeholder="MBBS" /></label>
      <label>Who issued it<input value={f.issuer} maxLength={120} onChange={(e) => set("issuer", e.target.value)} /></label>
      <label>Registration or certificate number (optional)<input value={f.reference} maxLength={64} onChange={(e) => set("reference", e.target.value)} /></label>
      <label>Obtained on (optional)<input type="date" value={f.obtained_on} onChange={(e) => set("obtained_on", e.target.value)} /></label>
      <label>Valid until (optional)<input type="date" value={f.expires_on} onChange={(e) => set("expires_on", e.target.value)} /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || f.title.trim().length < 2 || f.issuer.trim().length < 2}>{busy ? "Saving…" : "Add qualification"}</button>
    </form>
  );
}
