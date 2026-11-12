import { useCallback, useEffect, useState } from "react";
import { addPartner, listInboundEvents, listPartners, partnerStep } from "../api";
import type { InboundEvent, PartnerClient } from "../types";
import { Card, Pill, type PillKind } from "../ui";

const CLIENT_STATE: Record<PartnerClient["state"], { text: string; kind: PillKind }> = {
  active: { text: "Allowed to send", kind: "ok" }, suspended: { text: "Suspended", kind: "info" },
  revoked: { text: "Revoked", kind: "bad" },
};
const REASON: Record<string, string> = {
  schema: "The message was not the expected shape", unsupported_event: "That kind of message is not allowed for this partner",
};

function PartnerRow({ c, onDone }: { c: PartnerClient; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [secret, setSecret] = useState("");
  async function run(step: "suspend" | "resume" | "revoke" | "rotate-secret") {
    setBusy(true); setError("");
    try {
      const done = await partnerStep(c.public_id, step);
      setSecret(step === "rotate-secret" ? done.secret ?? "" : "");
      await onDone();
    } catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{c.label} <Pill kind={CLIENT_STATE[c.state].kind}>{CLIENT_STATE[c.state].text}</Pill></b>
        <small>Key {c.key_id}. May send: {c.allowed_types.join(" ")}. Up to {c.rate_per_minute} a minute. {c.last_used_at ? `Last heard from ${c.last_used_at.slice(0, 16).replace("T", " ")}.` : "Never used."}</small>
        {secret && <small role="status">New secret (shown once, copy it now): <b>{secret}</b></small>}
        {error && <small style={{ color: "var(--bad)" }} role="alert">{error}</small>}
      </div>
      {c.state !== "revoked" && (
        <div className="form-row">
          {c.state === "active" ? <button className="btn ghost" disabled={busy} onClick={() => void run("suspend")}>Suspend</button>
            : <button className="btn ghost" disabled={busy} onClick={() => void run("resume")}>Resume</button>}
          <button className="btn ghost" disabled={busy} onClick={() => void run("rotate-secret")}>New secret</button>
          <button className="btn ghost" disabled={busy} onClick={() => void run("revoke")}>Revoke</button>
        </div>
      )}
    </div>
  );
}

function NewPartner({ onDone }: { onDone: () => Promise<void> }) {
  const [label, setLabel] = useState("");
  const [types, setTypes] = useState("partner.");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{ key: string; secret: string } | null>(null);
  async function save(ev: React.FormEvent) {
    ev.preventDefault();
    setBusy(true); setError(""); setCreated(null);
    try {
      const made = await addPartner(label, types.split(/[\s,]+/).filter(Boolean));
      setCreated({ key: made.key_id, secret: made.secret ?? "" });
      setLabel("");
      await onDone();
    } catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Let a partner send us messages" sub="The partner signs each message with a secret only they and you hold. A message is checked before anything in it is read, and it never changes a booking or a record by itself. Each kind they may send must start with &quot;partner.&quot;.">
      <form className="profile-form" onSubmit={(ev) => void save(ev)}>
        <label>Partner's name<input value={label} maxLength={80} onChange={(ev) => setLabel(ev.target.value)} required /></label>
        <label>Kinds of message allowed (separate with spaces)
          <input value={types} onChange={(ev) => setTypes(ev.target.value)} placeholder="partner.lab." required />
        </label>
        {error && <div className="error" role="alert">{error}</div>}
        {created && <div className="success" role="status">Added. Give the partner this key <b>{created.key}</b> and this secret <b>{created.secret}</b>. The secret is shown only now.</div>}
        <div><button className="btn" type="submit" disabled={busy}>{busy ? "Adding…" : "Add the partner"}</button></div>
      </form>
    </Card>
  );
}

/** Partners that may send signed messages to this clinic, and what arrived. Administrators only. */
export function Partners() {
  const [clients, setClients] = useState<PartnerClient[] | null>(null);
  const [events, setEvents] = useState<InboundEvent[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setClients(await listPartners()); setEvents(await listInboundEvents()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the partners"); }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);
  const held = events.filter((e) => e.state === "quarantined").length;
  return (
    <>
      {error && <div className="error" role="alert">{error}</div>}
      <NewPartner onDone={load} />
      <Card title={`${clients?.length ?? 0} partner${clients?.length === 1 ? "" : "s"}`}>
        {clients === null ? <div className="empty">Loading…</div> : clients.length === 0
          ? <div className="empty">No partner has been added.</div>
          : clients.map((c) => <PartnerRow key={c.public_id} c={c} onDone={load} />)}
      </Card>
      <Card title={`Messages received${held ? ` (${held} held)` : ""}`} sub="Messages are only recorded here. Nothing acts on them yet.">
        {events.length === 0 ? <div className="empty">Nothing received yet.</div> : events.map((e) => (
          <div key={e.public_id} className="appt"><div className="info">
            <b>{e.event_type || "(unreadable)"} <Pill kind={e.state === "received" ? "ok" : "warn"}>{e.state === "received" ? "Received" : "Held"}</Pill></b>
            <small>From {e.client_label}. {e.created_at.slice(0, 16).replace("T", " ")}.{e.reason ? ` ${REASON[e.reason] ?? e.reason}.` : ""}</small>
          </div></div>
        ))}
      </Card>
    </>
  );
}
