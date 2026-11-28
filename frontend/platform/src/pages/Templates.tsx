import { useCallback, useEffect, useState } from "react";
import { getTemplates, signTemplate, type MessageTemplate, type TemplatesData } from "../api";
import { Card, Pill } from "../ui";

// Message templates (DEV-386, GHE-16 WF-15): every email and text Zoiko sends is fixed wording with
// named gaps. A version is sent only after a safety officer (clinical) AND a different person for
// privacy approve it; until then messages built from it are held, and go the moment it is approved.

const CLASS: Record<string, string> = {
  security: "Security notice", service: "Service update", clinical_alert: "Clinical alert", admin: "Staff admin",
};
const MODE: Record<string, string> = {
  enforce: "Enforced: messages from an unapproved version are held until it is approved.",
  report: "Report only (development): sent anyway, and logged.",
  off: "Off: not checked.",
};

/** Line-by-line: what was approved before, and what it says now. */
function Diff({ before, now }: { before: string; now: string }) {
  const old = new Set(before.split("\n"));
  const fresh = new Set(now.split("\n"));
  return (
    <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, margin: 0 }} aria-label="Changes since the last approved version">
      {before.split("\n").filter((l) => !fresh.has(l)).map((l, i) => <div key={`o${i}`} style={{ color: "var(--bad)" }}>- {l}</div>)}
      {now.split("\n").filter((l) => !old.has(l)).map((l, i) => <div key={`n${i}`} style={{ color: "var(--ok)" }}>+ {l}</div>)}
    </pre>
  );
}

function Row({ t, mayAs, onSigned }: { t: MessageTemplate; mayAs: string[]; onSigned: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  async function sign(as: "clinical" | "privacy") {
    setBusy(true); setError("");
    try {
      const r = await signTemplate(t.channel, t.key, as);
      onSigned(r.approved ? `${t.key} approved${r.released ? `: ${r.released} held message(s) sent` : ""}.` : `${t.key}: your sign-off is recorded. It needs the other one.`);
    } catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "POLICY_DENIED" ? "You cannot give both sign-offs, and only your own role's part."
        : code === "CONFLICT_VERSION" ? "That sign-off was already given. Reload." : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b><code>{t.key}</code> · {t.channel} · {CLASS[t.class] ?? t.class}{" "}
          <Pill kind={t.approved ? "ok" : "warn"}>{t.approved ? "Approved" : "Not approved"}</Pill>
          {t.held > 0 && <> <Pill kind="bad">{t.held} held</Pill></>}</b>
        <small>Version {t.version} · gaps: {t.variables.length ? t.variables.join(", ") : "none"} · health information: {t.exposure}
          {t.clinical_by ? ` · clinical: ${t.clinical_by}` : ""}{t.privacy_by ? ` · privacy: ${t.privacy_by}` : ""}</small>
      </div>
      <div><button className="btn ghost" onClick={() => setOpen(!open)}>{open ? "Hide the wording" : "Read the wording"}</button></div>
      {open && (
        <>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, background: "var(--bg)", padding: 10, borderRadius: 8 }}>{t.wording}</pre>
          {t.previous_wording && !t.approved && (
            <div><small>Changed since the last approved version:</small><Diff before={t.previous_wording} now={t.wording} /></div>
          )}
        </>
      )}
      {!t.approved && (
        <div className="form-row">
          {mayAs.includes("clinical") && !t.clinical_by && <button className="btn" disabled={busy} onClick={() => void sign("clinical")}>Approve as clinical safety</button>}
          {mayAs.includes("privacy") && !t.privacy_by && <button className="btn" disabled={busy} onClick={() => void sign("privacy")}>Approve as privacy</button>}
        </div>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}

export function Templates() {
  const [data, setData] = useState<TemplatesData | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const load = useCallback(async () => {
    try { setData(await getTemplates()); } catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const waiting = (data?.templates ?? []).filter((t) => !t.approved).length;
  return (
    <>
      <div className="hero"><div>
        <h1>Message templates</h1>
        <p>Every email and text Zoiko sends, word for word. Only the named gaps change, and each gap has a fixed shape,
          so no care details can be added. A version is sent only once a safety officer and, separately, privacy have
          approved it. Any change to the words is a new version.</p>
        {data && <p><Pill kind={data.mode === "enforce" ? "ok" : "warn"}>{data.mode}</Pill> {MODE[data.mode] ?? ""} {waiting} not approved.</p>}
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <div className="success" role="status">{done}</div>}
      <Card title="Templates" sub="Not approved first.">
        {!data ? <p>Loading…</p> : [...data.templates].sort((a, b) => Number(a.approved) - Number(b.approved)).map((t) => (
          <Row key={`${t.channel}:${t.key}`} t={t} mayAs={data.may_sign_as} onSigned={(m) => { setDone(m); void load(); }} />
        ))}
      </Card>
    </>
  );
}
