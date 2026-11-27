import { useState } from "react";
import { addService, askForListing, getOverview, getServices } from "../api";
import type { PartnerService } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const LISTING_TEXT: Record<string, string> = {
  draft: "Not listed", evidence_pending: "Waiting for a review", reviewed: "Reviewed, waiting to be published",
  published: "Listed", suspended: "Withdrawn",
};

function AddService({ organization, onDone }: { organization: string; onDone: () => void }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [minutes, setMinutes] = useState("30");
  const [mode, setMode] = useState("in_person");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await addService({ organization, name, category, duration_minutes: Number(minutes), mode, description });
      setName(""); setCategory(""); setDescription("");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the service");
    } finally { setBusy(false); }
  }

  return (
    <form className="ref-form" onSubmit={(e) => void submit(e)}>
      <label>Service name<input className="field" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></label>
      <label>Category<input className="field" value={category} onChange={(e) => setCategory(e.target.value)} required maxLength={60} /></label>
      <label>How long it takes (minutes)<input className="field" type="number" min={5} max={480} value={minutes} onChange={(e) => setMinutes(e.target.value)} required /></label>
      <label>How it is given
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="in_person">In person</option>
          <option value="video">By video</option>
        </select>
      </label>
      <label>What it is (optional)<textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || !name || !category}>{busy ? "Adding…" : "Add service"}</button>
    </form>
  );
}

function Row({ s, onChanged }: { s: PartnerService; onChanged: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function ask() {
    setBusy(true); setError("");
    try { await askForListing(s.id, note); setNote(""); onChanged(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not ask for a listing"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{s.name} <Pill kind={s.listing_state === "published" ? "ok" : "info"}>{LISTING_TEXT[s.listing_state] ?? s.listing_state}</Pill></b>
        <small>{s.category} · {s.duration_minutes} minutes · {s.mode === "video" ? "by video" : "in person"}</small>
        {s.listing?.evidence_note && <small>Evidence you gave: {s.listing.evidence_note}</small>}
      </div>
      {s.listing_state === "draft" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input className="field" aria-label={`Evidence for listing ${s.name}`} placeholder="Evidence reference (licence, accreditation)"
            value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
          <button className="btn" disabled={busy || note.trim().length < 3} onClick={() => void ask()}>Ask for a listing</button>
        </div>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}

export function Services() {
  const [n, setN] = useState(0);              // changing the key loads everything again
  return <ServicesInner key={n} onChanged={() => setN(n + 1)} />;
}

function ServicesInner({ onChanged }: { onChanged: () => void }) {
  const overview = useLoad(getOverview, 120_000);
  const list = useLoad(getServices, 30_000);
  const organization = overview.data?.roles[0]?.organization.id ?? "";
  return (
    <>
      <div className="hero"><div>
        <h1>My services</h1>
        <p>The services your organisation offers. A service can be found by others only after it is listed, and a listing is reviewed and published by an administrator who did not ask for it.</p>
      </div></div>
      <div className="grid">
        <Card title="Your services">
          <Gate result={list}>
            {(rows) => rows.length === 0
              ? <div className="empty">No services yet. Add the first one.</div>
              : rows.map((s) => <Row key={s.id} s={s} onChanged={onChanged} />)}
          </Gate>
        </Card>
        <Card title="Add a service">
          {organization
            ? <AddService organization={organization} onDone={onChanged} />
            : <div className="empty">Loading your organisation…</div>}
        </Card>
      </div>
    </>
  );
}
