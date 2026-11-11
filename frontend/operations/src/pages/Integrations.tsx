import { useState } from "react";
import { createApiClient, getDeveloper, redriveDelivery, revokeApiClient, testWebhook } from "../api";
import type { DeveloperClient, DeveloperData, DeveloperDelivery, DeveloperWebhook } from "../consoleTypes";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, type PillKind } from "../ui";

const SCOPE: Record<string, string> = {
  "catalog:read": "Read the published service list",
  "webhooks:manage": "Create and control its own webhooks",
  "events:read": "Receive event notices (references only)",
};

const DELIVERY: Record<DeveloperDelivery["state"], { word: string; kind: PillKind }> = {
  pending: { word: "Waiting to be sent", kind: "info" },
  delivered: { word: "Delivered", kind: "ok" },
  dead: { word: "Given up", kind: "bad" },
};

const HOOK: Record<DeveloperWebhook["status"], { word: string; kind: PillKind }> = {
  active: { word: "Active", kind: "ok" },
  paused: { word: "Paused", kind: "warn" },
  disabled: { word: "Switched off after repeated failures", kind: "bad" },
};

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "");

export function Integrations() {
  const [version, setVersion] = useState(0);
  const result = useLoad(getDeveloper, 15_000, version);
  return <Gate result={result}>{(d) => <View data={d} onChanged={() => setVersion((v) => v + 1)} />}</Gate>;
}

function View({ data, onChanged }: { data: DeveloperData; onChanged: () => void }) {
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [fresh, setFresh] = useState<{ name: string; key: string } | null>(null);
  const [form, setForm] = useState({ name: "", environment: "sandbox", scopes: ["catalog:read"] as string[] });

  async function run(key: string, work: () => Promise<unknown>, success: string) {
    setBusy(key); setError(""); setNote("");
    try { await work(); setNote(success); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }

  async function create() {
    setBusy("new"); setError(""); setNote(""); setFresh(null);
    try {
      const made = await createApiClient(form);
      setFresh({ name: made.client.name, key: made.key });
      setForm({ name: "", environment: "sandbox", scopes: ["catalog:read"] });
      onChanged();
    } catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); }
  }

  const toggle = (scope: string) => setForm({
    ...form, scopes: form.scopes.includes(scope) ? form.scopes.filter((s) => s !== scope) : [...form.scopes, scope],
  });

  return (
    <>
      <div className="hero"><div>
        <h1>Integrations</h1>
        <p>Where partners connect. A partner gets an API key with only the permissions you tick: a key never opens
          anyone's record. A sandbox key only ever sees made-up data. Webhooks tell a partner that something happened
          (a reference and a type, never patient details) and are signed so the partner can check they are genuine.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {note && <div className="success" role="status">{note}</div>}
      {fresh && (
        <div className="notice" role="status">
          <b>Key for {fresh.name}: copy it now.</b> It cannot be shown again; only a fingerprint is kept.
          <div className="form-row">
            <input aria-label="The new API key" readOnly value={fresh.key} onFocus={(e) => e.currentTarget.select()} />
            <button className="btn ghost" onClick={() => setFresh(null)}>I have saved it</button>
          </div>
        </div>
      )}
      <div className="grid">
        {data.can_manage && (
          <Card title="Create an API client" sub="One client for each partner. You can switch it off at any time.">
            <div className="form-row">
              <input aria-label="Client name" placeholder="Partner or application name" value={form.name} maxLength={100}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
              <select aria-label="Environment" value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
                <option value="sandbox">Sandbox: made-up data only</option>
                <option value="production">Production: this clinic's published services</option>
              </select>
            </div>
            <fieldset style={{ border: 0, padding: 0, margin: "12px 0 0" }}>
              <legend>What it may do</legend>
              {data.scopes.map((s) => (
                <label key={s} style={{ display: "block", margin: "6px 0" }}>
                  <input type="checkbox" checked={form.scopes.includes(s)} onChange={() => toggle(s)} />{" "}
                  {SCOPE[s] ?? s} <small>({s})</small>
                </label>
              ))}
            </fieldset>
            <div className="form-row">
              <button className="btn" disabled={busy === "new" || !form.name.trim() || form.scopes.length === 0} onClick={() => void create()}>Create client</button>
            </div>
          </Card>
        )}
        <Card title={`API clients (${data.clients.length})`} sub={`Events a partner can hear about: ${data.event_families.map((f) => f.replace(/\.$/, "")).join(", ")}.`}>
          {data.clients.length === 0 ? <div className="empty">No partner is connected.</div> : data.clients.map((c) => (
            <Client key={c.public_id} c={c} canManage={data.can_manage} busy={busy} run={run} />
          ))}
        </Card>
      </div>
    </>
  );
}

function Client({ c, canManage, busy, run }: {
  c: DeveloperClient; canManage: boolean; busy: string;
  run: (key: string, work: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{c.name}{" "}
          <Pill kind={c.environment === "sandbox" ? "info" : "warn"}>{c.environment === "sandbox" ? "Sandbox" : "Production"}</Pill>{" "}
          <Pill kind={c.status === "active" ? "ok" : "bad"}>{c.status === "active" ? "Active" : "Revoked"}</Pill>
        </b>
        <small>May: {c.scopes.map((s) => SCOPE[s] ?? s).join("; ")}.</small>
        <small>Created {when(c.created_at)}. {c.last_used_at ? `Last used ${when(c.last_used_at)}.` : "Never used."}</small>
      </div>
      {canManage && c.status === "active" && (
        <div className="form-row">
          {!confirming
            ? <button className="btn ghost" onClick={() => setConfirming(true)}>Revoke<span className="sr-only"> {c.name}</span></button>
            : (
              <>
                <span>Its key stops working at once and its webhooks stop.</span>
                <button className="btn" disabled={busy === c.public_id}
                  onClick={() => { setConfirming(false); void run(c.public_id, () => revokeApiClient(c.public_id), `${c.name} was revoked.`); }}>
                  Yes, revoke {c.name}
                </button>
                <button className="btn ghost" onClick={() => setConfirming(false)}>Cancel</button>
              </>
            )}
        </div>
      )}
      {c.webhooks.length === 0
        ? <div className="sub">No webhooks yet. The partner creates them itself with its key.</div>
        : c.webhooks.map((h) => <Hook key={h.public_id} clientId={c.public_id} h={h} canManage={canManage && c.status === "active"} busy={busy} run={run} />)}
    </div>
  );
}

function Hook({ clientId, h, canManage, busy, run }: {
  clientId: string; h: DeveloperWebhook; canManage: boolean; busy: string;
  run: (key: string, work: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const s = HOOK[h.status];
  return (
    <div className="sub" style={{ borderTop: "1px solid var(--line)", paddingTop: 8, marginTop: 8 }}>
      <b>{h.url}</b> <Pill kind={s.kind}>{s.word}</Pill>
      <div>Hears about: {h.event_prefixes.map((p) => p.replace(/\.$/, "")).join(", ")}.
        {h.dead_streak > 0 && ` ${h.dead_streak} ${h.dead_streak === 1 ? "delivery was" : "deliveries were"} given up in a row.`}</div>
      {canManage && (
        <div className="form-row">
          <button className="btn ghost" disabled={busy === h.public_id}
            onClick={() => void run(h.public_id, () => testWebhook(clientId, h.public_id), "A test message was queued. It is sent within a few seconds.")}>
            Send a test message<span className="sr-only"> to {h.url}</span>
          </button>
        </div>
      )}
      {h.deliveries.length === 0 ? <div>No deliveries yet.</div> : (
        <ul>
          {h.deliveries.map((d) => (
            <li key={d.public_id}>
              {d.event_type}: <Pill kind={DELIVERY[d.state].kind}>{DELIVERY[d.state].word}</Pill>
              {" "}after {d.attempts} attempt{d.attempts === 1 ? "" : "s"}
              {d.last_status ? `, last answer ${d.last_status}` : ""}{d.last_error ? `, problem: ${d.last_error}` : ""}
              {canManage && d.state === "dead" && (
                <>{" "}<button className="btn ghost" disabled={busy === d.public_id}
                  onClick={() => void run(d.public_id, () => redriveDelivery(clientId, d.public_id), "Queued to be sent again.")}>
                  Send again<span className="sr-only"> {d.event_type}</span></button></>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
