import { useState } from "react";
import { addHook, getDeliveries, getHooks, hookStep } from "../api";
import type { Hook } from "../types";
import { Card, Pill, Table } from "../ui";
import { Gate, useLoad } from "../useLoad";

const STATE: Record<string, string> = {
  pending_verification: "not yet verified", active: "active", paused: "paused", revoked: "revoked",
};
const DELIVERY: Record<string, string> = {
  pending: "waiting", retrying: "retrying", delivered: "delivered", dead_letter: "gave up",
};

function Row({ h, onChanged }: { h: Hook; onChanged: (said?: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(step: "verify" | "pause" | "resume" | "revoke" | "rotate-secret") {
    setBusy(true); setError("");
    try {
      const done = await hookStep(h.public_id, step);
      onChanged(done.secret ? `New secret for ${h.label}: ${done.secret}` : undefined);
    } catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{h.label} <Pill kind={h.state === "active" ? "ok" : "warn"}>{STATE[h.state] ?? h.state}</Pill></b>
        <small>{h.url} · {h.topics.join(", ") || "no topics"}{h.failure_streak > 0 ? ` · ${h.failure_streak} failures in a row` : ""}</small>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {h.state !== "revoked" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {h.state === "pending_verification" && <button className="btn" disabled={busy} onClick={() => void run("verify")}>Verify now</button>}
          {h.state === "active" && <button className="btn ghost" disabled={busy} onClick={() => void run("pause")}>Pause</button>}
          {h.state === "paused" && <button className="btn ghost" disabled={busy} onClick={() => void run("resume")}>Resume</button>}
          <button className="btn ghost" disabled={busy} onClick={() => void run("rotate-secret")}>New secret</button>
          <button className="btn ghost" disabled={busy} onClick={() => void run("revoke")}>Revoke</button>
        </div>
      )}
    </div>
  );
}

function Add({ onDone }: { onDone: (said?: string) => void }) {
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("https://");
  const [topics, setTopics] = useState("booking.");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const made = await addHook(label, url, topics.split(/[\s,;]+/).filter(Boolean));
      setLabel("");
      onDone(`Secret for ${made.label}, shown once: ${made.secret}`);
    } catch (err) {
      setError((err as { code?: string }).code === "VALIDATION_FAILED"
        ? "Check the address (it must be https and reachable from outside) and the topics (words ending in a dot, like booking.)."
        : err instanceof Error ? err.message : "Could not add the webhook");
    } finally { setBusy(false); }
  }
  return (
    <form className="ref-form" onSubmit={(e) => void submit(e)}>
      <label>Name<input className="field" value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={80} /></label>
      <label>Address we send events to<input className="field" type="url" value={url} onChange={(e) => setUrl(e.target.value)} required /></label>
      <label>Topics (separated by commas)<input className="field" value={topics} onChange={(e) => setTopics(e.target.value)} required /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || !label.trim()}>{busy ? "Adding…" : "Add webhook"}</button>
    </form>
  );
}

export function Webhooks() {
  const [n, setN] = useState(0);
  const [said, setSaid] = useState("");
  return <WebhooksInner key={n} said={said} onChanged={(m) => { setSaid(m ?? ""); setN(n + 1); }} />;
}

function WebhooksInner({ onChanged, said }: { onChanged: (said?: string) => void; said: string }) {
  const hooks = useLoad(getHooks, 60_000);
  const deliveries = useLoad(getDeliveries, 30_000);
  return (
    <>
      <div className="hero"><div>
        <h1>Webhooks</h1>
        <p>Addresses Zoiko Access sends signed event messages to. Each message is signed so you can check it came from us. A secret is shown once.</p>
      </div></div>
      {said && <div className="notice" role="status" data-testid="secret-note">{said}</div>}
      <div className="grid">
        <Card title="Your webhooks"><Gate result={hooks}>{(rows) => rows.length === 0 ? <div className="empty">No webhooks yet.</div> : rows.map((h) => <Row key={h.public_id} h={h} onChanged={onChanged} />)}</Gate></Card>
        <Card title="Add a webhook"><Add onDone={onChanged} /></Card>
        <Card title="Recent deliveries">
          <Gate result={deliveries}>
            {(rows) => <Table label="Recent deliveries" headers={["Event", "Result", "Tries", "Sent"]}
              rows={rows.map((d) => [d.event_type, DELIVERY[d.state] ?? d.state, String(d.attempts), d.created_at.slice(0, 16).replace("T", " ")])}
              empty="Nothing has been sent yet." />}
          </Gate>
        </Card>
      </div>
    </>
  );
}
