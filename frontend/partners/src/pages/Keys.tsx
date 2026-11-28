import { useState } from "react";
import { addKey, getKeys, keyStep } from "../api";
import type { PartnerKey } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const STATE: Record<string, string> = { pending: "waiting for Zoiko's checks", active: "active", suspended: "suspended", revoked: "revoked" };
const CHECK_NAME = { identity: "Identity", legal: "Legal", security: "Security" } as const;

function Row({ k, onChanged }: { k: PartnerKey; onChanged: (secret?: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(step: "suspend" | "resume" | "revoke" | "rotate-secret" | "issue-secret") {
    setBusy(true); setError("");
    try {
      const done = await keyStep(k.public_id, step);
      onChanged(done.secret ? `${step === "issue-secret" ? "Secret" : "New secret"} for ${k.label}, shown once: ${done.secret}` : undefined);
    } catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{k.label} <Pill kind={k.state === "active" ? "ok" : "warn"}>{STATE[k.state] ?? k.state}</Pill></b>
        {k.state === "pending" && (
          <small data-testid="checks">Zoiko records three checks before a secret exists: {(Object.keys(CHECK_NAME) as (keyof typeof CHECK_NAME)[]).map((c) => `${CHECK_NAME[c]} ${k.checks[c] ? "done" : "waiting"}`).join(", ")}.</small>
        )}
        <small>{k.environment === "sandbox" ? "Sandbox key (made-up data only)" : "Live key"} · may {(k.scopes ?? ["events:send"]).map((x) => x === "catalog:read" ? "read services" : "send events").join(" and ")}</small>
        <small>Key id {k.key_id} · may send {k.allowed_types.join(", ") || "nothing"} · {k.rate_per_minute} a minute{k.last_used_at ? ` · last used ${k.last_used_at.slice(0, 16).replace("T", " ")}` : " · never used"}</small>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {k.state === "pending" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" disabled={busy || !k.checks.identity || !k.checks.legal || !k.checks.security || !k.checks.sandbox_test} onClick={() => void run("issue-secret")}>Get the secret</button>
          <button className="btn ghost" disabled={busy} onClick={() => void run("revoke")}>Revoke</button>
        </div>
      )}
      {k.state !== "revoked" && k.state !== "pending" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {k.state === "active"
            ? <button className="btn ghost" disabled={busy} onClick={() => void run("suspend")}>Suspend</button>
            : <button className="btn ghost" disabled={busy} onClick={() => void run("resume")}>Resume</button>}
          <button className="btn ghost" disabled={busy} onClick={() => void run("rotate-secret")}>New secret</button>
          <button className="btn ghost" disabled={busy} onClick={() => void run("revoke")}>Revoke</button>
        </div>
      )}
    </div>
  );
}

function Add({ onDone }: { onDone: (secret?: string) => void }) {
  const [label, setLabel] = useState("");
  const [types, setTypes] = useState("partner.booking.");
  const [scopes, setScopes] = useState<string[]>(["events:send"]);
  const [environment, setEnvironment] = useState<"live" | "sandbox">("sandbox");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const made = await addKey(label, types.split(/[\s,;]+/).filter(Boolean), scopes, environment);
      setLabel("");
      onDone(`${made.label} was added. It has no secret yet: Zoiko checks who you are first, then you can get the secret here.`);
    } catch (err) {
      setError((err as { code?: string }).code === "VALIDATION_FAILED" ? "Check the name and the event types (they start with partner., like partner.booking.)." : err instanceof Error ? err.message : "Could not add the key");
    } finally { setBusy(false); }
  }
  return (
    <form className="ref-form" onSubmit={(e) => void submit(e)}>
      <label>Name<input className="field" value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={80} /></label>
      <label>Event types it may send (separated by commas)<input className="field" value={types} onChange={(e) => setTypes(e.target.value)} required /></label>
      <fieldset style={{ border: 0, padding: 0 }}>
        <legend>What the key may do</legend>
        {[["events:send", "Send events to Zoiko"], ["catalog:read", "Read the published services (public API)"]].map(([code, text]) => (
          <label key={code} style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" checked={scopes.includes(code)}
              onChange={(e) => setScopes((cur) => e.target.checked ? [...cur, code] : cur.filter((x) => x !== code))} />
            {text}
          </label>
        ))}
      </fieldset>
      <label>Environment
        <select className="field" value={environment} onChange={(e) => setEnvironment(e.target.value as "live" | "sandbox")}>
          <option value="sandbox">Sandbox: only made-up data (for building and testing)</option>
          <option value="live">Live: the customer's real published services</option>
        </select>
      </label>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || !label.trim() || scopes.length === 0}>{busy ? "Adding…" : "Add key"}</button>
    </form>
  );
}

export function Keys() {
  const [n, setN] = useState(0);
  const [said, setSaid] = useState("");
  return <KeysInner key={n} said={said} onChanged={(m) => { setSaid(m ?? ""); setN(n + 1); }} />;
}

function KeysInner({ onChanged, said }: { onChanged: (said?: string) => void; said: string }) {
  const keys = useLoad(getKeys, 60_000);
  return (
    <>
      <div className="hero"><div>
        <h1>API keys</h1>
        <p>The keys your systems use to send messages to Zoiko Access. Zoiko checks a new key (identity, legal, security) before any secret exists. The secret is then shown once: copy it then.</p>
      </div></div>
      {said && <div className="notice" role="status" data-testid="secret-note">{said}</div>}
      <div className="grid">
        <Card title="Your keys"><Gate result={keys}>{(rows) => rows.length === 0 ? <div className="empty">No keys yet.</div> : rows.map((k) => <Row key={k.public_id} k={k} onChanged={onChanged} />)}</Gate></Card>
        <Card title="Add a key"><Add onDone={onChanged} /></Card>
      </div>
    </>
  );
}
