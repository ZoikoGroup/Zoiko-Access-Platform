import { useCallback, useEffect, useState } from "react";
import { addWebhook, fhirQuarantineStep, listFhirQuarantine, listWebhookDeliveries, listWebhooks, redriveDelivery, webhookStep } from "../api";
import type { FhirQuarantineItem, FhirQuarantineState, WebhookDelivery, WebhookEndpoint } from "../types";
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
const QUARANTINE_STATE: Record<FhirQuarantineState, { text: string; kind: PillKind }> = {
  held: { text: "Quarantined", kind: "warn" },
  discarded: { text: "Discarded", kind: "bad" },
  resend_requested: { text: "Resend Requested", kind: "info" },
  released: { text: "Taken in after a retry", kind: "ok" },
};

function FhirQuarantineRow({ item, onDone }: { item: FhirQuarantineItem; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notePrompt, setNotePrompt] = useState<"discard" | "re-request" | null>(null);
  const [note, setNote] = useState("");

  async function retry() {
    setBusy(true); setError("");
    try {
      const done = await fhirQuarantineStep(item.public_id, "retry");
      if (done.state === "held") setError("Still held: " + (done.issues?.[0]?.text ?? "it does not pass the checks yet") + ".");
      else await onDone();
    } catch (err) { setError(err instanceof Error ? err.message : "Action failed"); }
    finally { setBusy(false); }
  }

  async function handleStep(step: "discard" | "re-request") {
    setBusy(true);
    setError("");
    try {
      await fhirQuarantineStep(item.public_id, step, note.trim() || undefined);
      setNotePrompt(null);
      setNote("");
      await onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  const badge = QUARANTINE_STATE[item.state] ?? { text: item.state, kind: "info" };

  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>
          {item.resource_type || "Resource"}{" "}
          <Pill kind={badge.kind}>{badge.text}</Pill>
        </b>
        <small>
          Partner: <b>{item.partner_label || item.partner_client_id}</b> | Delivery ID: <code>{item.delivery_id}</code>
          {item.partner_ref ? ` | Ref: ${item.partner_ref}` : ""}
        </small>
        <small>
          Received: {item.created_at.slice(0, 16).replace("T", " ")} | Digest: <code>{item.body_digest.slice(0, 10)}</code>
        </small>
        {item.issues && item.issues.length > 0 && (
          <div style={{ marginTop: "4px" }}>
            <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>Validation Issues:</span>
            <ul style={{ margin: "4px 0", paddingLeft: 20, fontSize: "0.85rem" }}>
              {item.issues.map((iss, idx) => (
                <li key={idx}>
                  <code>{iss.path || "root"}</code>: {iss.text} {iss.code ? `(${iss.code})` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
        {item.decided_at && (
          <small style={{ color: "var(--muted)" }}>
            Decided by {item.decided_by} on {item.decided_at.slice(0, 16).replace("T", " ")}
            {item.decision_note ? `: "${item.decision_note}"` : ""}
          </small>
        )}
        {error && <small style={{ color: "var(--bad)" }} role="alert">{error}</small>}
      </div>

      {item.state === "held" && (
        <div style={{ marginTop: "8px" }}>
          {notePrompt ? (
            <div className="form-row" style={{ alignItems: "center", gap: "8px" }}>
              <input
                type="text"
                placeholder={notePrompt === "discard" ? "Reason for discarding (optional)…" : "Note for resend request (optional)…"}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={200}
                style={{ flex: 1 }}
              />
              <button
                className={`btn ${notePrompt === "discard" ? "bad" : ""}`}
                disabled={busy}
                onClick={() => void handleStep(notePrompt)}
              >
                {busy ? "Saving…" : notePrompt === "discard" ? "Confirm Discard" : "Confirm Request"}
              </button>
              <button className="btn ghost" disabled={busy} onClick={() => { setNotePrompt(null); setNote(""); }}>
                Cancel
              </button>
            </div>
          ) : (
            <div className="form-row">
              <button className="btn ghost" disabled={busy} onClick={() => setNotePrompt("discard")}>
                Discard
              </button>
              <button className="btn ghost" disabled={busy} onClick={() => setNotePrompt("re-request")}>
                Ask to resend
              </button>
              <button className="btn ghost" disabled={busy} onClick={() => void retry()} title="Check it again, for example after a code match was approved">
                Try again
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FhirQuarantineCard() {
  const [items, setItems] = useState<FhirQuarantineItem[] | null>(null);
  const [filter, setFilter] = useState<string>("held");
  const [more, setMore] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await listFhirQuarantine(filter === "all" ? undefined : filter);
      setItems(res.items);
      setMore(res.next_cursor ?? null);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load quarantine items");
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const countHeld = items?.filter((i) => i.state === "held").length ?? 0;

  return (
    <Card
      title={`FHIR Quarantine${countHeld > 0 ? ` (${countHeld} held)` : ""}`}
      sub="Signed FHIR R4 resources from partners that failed schema validation, reference integrity, or mapping. Zero clinical state is updated until triaged."
    >
      <div className="form-row" style={{ marginBottom: "12px", gap: "8px" }}>
        <button
          className={`btn ${filter === "held" ? "" : "ghost"}`}
          onClick={() => setFilter("held")}
        >
          Held in Quarantine
        </button>
        <button
          className={`btn ${filter === "all" ? "" : "ghost"}`}
          onClick={() => setFilter("all")}
        >
          All History
        </button>
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      {items === null ? (
        <div className="empty">Loading quarantine…</div>
      ) : items.length === 0 ? (
        <div className="empty">No quarantined messages found.</div>
      ) : (
        items.map((item) => <FhirQuarantineRow key={item.public_id} item={item} onDone={load} />)
      )}
      {more && (
        <button className="btn ghost" onClick={() => void (async () => {
          try {
            const res = await listFhirQuarantine(filter === "all" ? undefined : filter, more);
            setItems([...(items ?? []), ...res.items]); setMore(res.next_cursor ?? null);
          } catch (e) { setError(e instanceof Error ? e.message : "Could not load older messages"); }
        })()}>Show older messages</button>
      )}
    </Card>
  );
}


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
      const notApproved = (err as { code?: string }).code === "POLICY_DENIED";
      setError(step === "verify" && notApproved
        ? "Zoiko has not approved this address yet, so nothing is sent to it. You will be able to check it once it is approved."
        : step === "verify"
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
        {e.address_permit && e.address_permit !== "approved" && (
          <small><Pill kind={e.address_permit === "proposed" ? "warn" : "bad"}>
            {e.address_permit === "proposed" ? "Waiting for Zoiko to approve this address"
              : e.address_permit === "none" ? "This address cannot be approved (use a host name, not an IP number)"
              : `Zoiko ${e.address_permit} this address`}</Pill> Nothing is sent to it until Zoiko approves it.</small>
        )}
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
  const [more, setMore] = useState<string | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setEndpoints(await listWebhooks());
      const page = await listWebhookDeliveries();
      setRows(page.deliveries); setMore(page.next_cursor ?? null);
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
          {more && (
            <button className="btn ghost" onClick={() => void (async () => {
              try { const page = await listWebhookDeliveries(more); setRows([...rows, ...page.deliveries]); setMore(page.next_cursor ?? null); }
              catch (e) { setError(e instanceof Error ? e.message : "Could not load older deliveries"); }
            })()}>Show older deliveries</button>
          )}
        </Card>
        <FhirQuarantineCard />
        <Partners />
      </div>
    </>
  );
}
