import { useCallback, useEffect, useState } from "react";
import { addWebhook, listWebhookDeliveries, listWebhooks, redriveDelivery, webhookStep } from "../api";
import type { WebhookDelivery, WebhookEndpoint } from "../types";
import { Card, Pill, type PillKind } from "../ui";
import { Partners } from "./Partners";

// The kinds of event a partner can ask for: the families the system really emits (GHE-13).
const TOPICS: [string, string][] = [
  ["booking.", "Appointments"], ["message.", "Messages"], ["waitlist.", "Waiting list"],
  ["slot.", "Free times"], ["family.", "Family requests"], ["delegation.", "Family access"],
  ["professional.", "Doctor status"],
];
const ENDPOINT_STATE: Record<WebhookEndpoint["state"], { text: string; kind: PillKind }> = {
  pending_verification: { text: "Not verified yet", kind: "warn" },
  active: { text: "Sending", kind: "ok" },
  paused: { text: "Paused", kind: "info" },
  revoked: { text: "Revoked", kind: "bad" },
};
const DELIVERY_STATE: Record<WebhookDelivery["state"], { text: string; kind: PillKind }> = {
  pending: { text: "Waiting", kind: "info" }, retrying: { text: "Will retry", kind: "warn" },
  delivered: { text: "Received", kind: "ok" }, dead_letter: { text: "Gave up", kind: "bad" },
};

function EndpointRow({ e, onDone }: { e: WebhookEndpoint; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [secret, setSecret] = useState("");
  async function run(step: "verify" | "pause" | "resume" | "revoke" | "rotate-secret") {
    setBusy(true); setError("");
    try {
      const done = await webhookStep(e.public_id, step);
      setSecret(step === "rotate-secret" ? done.secret ?? "" : "");
      await onDone();
    } catch (err) {
      setError(step === "verify"
        ? "The address did not answer the check correctly. It must reply with 2xx and repeat the challenge."
        : err instanceof Error ? err.message : "That did not work");
    } finally { setBusy(false); }
  }
  const revoked = e.state === "revoked";
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{e.label} <Pill kind={ENDPOINT_STATE[e.state].kind}>{ENDPOINT_STATE[e.state].text}</Pill></b>
        <small>{e.url}</small>
        <small>Sends: {e.topics.join(" ")}. Key {e.key_id}.{e.failure_streak > 0 ? ` ${e.failure_streak} deliveries gave up in a row.` : ""}</small>
        {secret && <small role="status">New secret (shown once, copy it now): <b>{secret}</b></small>}
        {error && <small style={{ color: "var(--bad)" }} role="alert">{error}</small>}
      </div>
      {!revoked && (
        <div className="form-row">
          <button className="btn ghost" disabled={busy} onClick={() => void run("verify")}>{e.verified_at ? "Check the address again" : "Check the address"}</button>
          {e.state === "active" && <button className="btn ghost" disabled={busy} onClick={() => void run("pause")}>Pause</button>}
          {e.state === "paused" && <button className="btn ghost" disabled={busy || !e.verified_at} onClick={() => void run("resume")}>Resume</button>}
          <button className="btn ghost" disabled={busy} onClick={() => void run("rotate-secret")}>New secret</button>
          <button className="btn ghost" disabled={busy} onClick={() => void run("revoke")}>Revoke</button>
        </div>
      )}
    </div>
  );
}

function NewEndpoint({ onDone }: { onDone: () => Promise<void> }) {
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("");
  const [topics, setTopics] = useState<string[]>(["booking."]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [secret, setSecret] = useState("");
  async function save(ev: React.FormEvent) {
    ev.preventDefault();
    setBusy(true); setError(""); setSecret("");
    try {
      const made = await addWebhook(label, url, topics);
      setSecret(made.secret ?? ""); setLabel(""); setUrl("");
      await onDone();
    } catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Add an address" sub="Zoiko sends a signed message to this address when something happens. The message holds references only (no names or health details); your system asks for details through the normal, signed-in API. It must be a public https address.">
      <form className="profile-form" onSubmit={(ev) => void save(ev)}>
        <label>Name<input value={label} maxLength={80} onChange={(ev) => setLabel(ev.target.value)} required /></label>
        <label>Address (https)<input type="url" value={url} maxLength={500} onChange={(ev) => setUrl(ev.target.value)} placeholder="https://" required /></label>
        <fieldset>
          <legend>What to send</legend>
          {TOPICS.map(([code, text]) => (
            <label key={code} className="check">
              <input type="checkbox" checked={topics.includes(code)}
                onChange={(ev) => setTopics(ev.target.checked ? [...topics, code] : topics.filter((t) => t !== code))} /> {text}
            </label>
          ))}
        </fieldset>
        {error && <div className="error" role="alert">{error}</div>}
        {secret && <div className="success" role="status">Added. Your signing secret is <b>{secret}</b>. It is shown only now; store it safely, then use "Check the address".</div>}
        <div><button className="btn" type="submit" disabled={busy || topics.length === 0}>{busy ? "Adding…" : "Add the address"}</button></div>
      </form>
    </Card>
  );
}

/** Outbound signed webhooks: addresses, and what happened to each delivery. Administrators only. */
export function Integrations() {
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[] | null>(null);
  const [rows, setRows] = useState<WebhookDelivery[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setEndpoints(await listWebhooks());
      setRows(await listWebhookDeliveries());
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load the integrations"); }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);
  async function redrive(id: string) {
    try { await redriveDelivery(id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
  }
  const parked = rows.filter((r) => r.state === "dead_letter");
  return (
    <>
      <div className="hero"><div>
        <h1>Integrations</h1>
        <p>Send signed messages about appointments, messages and the waiting list to a partner's system. Delivery is at least once: the partner must ignore an id it has already seen. A failed delivery is tried again with growing waits, then parked here for a person to send again. Only a clinic administrator can use this page.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <NewEndpoint onDone={load} />
        <Card title={`${endpoints?.length ?? 0} address${endpoints?.length === 1 ? "" : "es"}`}>
          {endpoints === null ? <div className="empty">Loading…</div> : endpoints.length === 0
            ? <div className="empty">No address added yet.</div>
            : endpoints.map((e) => <EndpointRow key={e.public_id} e={e} onDone={load} />)}
        </Card>
        <Card title={`Recent deliveries${parked.length ? ` (${parked.length} gave up)` : ""}`}>
          {rows.length === 0 ? <div className="empty">Nothing has been sent yet.</div> : rows.map((d) => (
            <div key={d.public_id} className="appt">
              <div className="info">
                <b>{d.event_type} <Pill kind={DELIVERY_STATE[d.state].kind}>{DELIVERY_STATE[d.state].text}</Pill></b>
                <small>{d.attempts} attempt{d.attempts === 1 ? "" : "s"}{d.last_status ? `, last answer ${d.last_status}` : ""}{d.last_error ? `, ${d.last_error}` : ""}. {d.created_at.slice(0, 16).replace("T", " ")}{d.owner_name ? ` Owner: ${d.owner_name}.` : ""}{d.body_digest ? ` Message fingerprint ${d.body_digest.slice(0, 10)}.` : ""}</small>
              </div>
              {d.state === "dead_letter" && <button className="btn ghost" onClick={() => void redrive(d.public_id)}>Send again</button>}
            </div>
          ))}
        </Card>
        <Partners />
      </div>
    </>
  );
}
