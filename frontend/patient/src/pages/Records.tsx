import { useEffect, useState } from "react";
import { downloadMyData, getMyHealth, getMyValues, type MyHealth, type MyValue } from "../api";
import { Card, Pill, StatCard } from "../ui";

const KIND: Record<string, string> = {
  consultation_note: "Consultation note", result: "Result", letter: "Letter", imaging: "Imaging", other: "Other record",
};
const REQUEST: Record<string, string> = {
  lab_order: "Test or scan", pharmacy_request: "Pharmacy", homecare_referral: "Home care",
};

function Values({ id }: { id: string }) {
  const [values, setValues] = useState<MyValue[] | null>(null);
  const [error, setError] = useState("");
  async function load() {
    setError("");
    try { setValues((await getMyValues(id)).values); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the values"); }
  }
  if (values === null) {
    return (
      <div>
        <button className="btn ghost" onClick={() => void load()}>Show the values</button>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
    );
  }
  if (values.length === 0) return <small>The service sent no measured values for this result.</small>;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Measured values">
      <table>
        <thead><tr><th>What</th><th>Value</th><th>Usual range</th><th></th></tr></thead>
        <tbody>
          {values.map((v, i) => (
            <tr key={`${v.name}-${i}`}>
              <td>{v.name}</td>
              <td>{String(v.value)} {v.unit ?? ""}</td>
              <td>{v.low != null || v.high != null ? `${v.low ?? "…"} to ${v.high ?? "…"}` : "-"}</td>
              <td>{v.flag && v.flag !== "normal" ? <Pill kind="warn">{v.flag}</Pill> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Everything the system holds about the patient's health, in one place, and a download of it. */
export function Records({ goTo }: { goTo: (page: "notes" | "requests") => void }) {
  const [data, setData] = useState<MyHealth | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getMyHealth().then(setData).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  }, []);
  async function download() {
    setBusy(true); setError("");
    try { await downloadMyData(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not prepare the file"); }
    finally { setBusy(false); }
  }
  const refs = data && Array.isArray(data.references) ? data.references : null;
  return (
    <>
      <div className="hero">
        <div>
          <h1>My Health Records</h1>
          <p>Your visit notes, your results and where clinics keep your records, in one place. Notes appear a day after your doctor signs them, and results' values a day after a doctor reviews them, so your doctor can talk to you first.</p>
        </div>
        <span className="spacer" />
        <button className="btn" disabled={busy} onClick={() => void download()}>{busy ? "Preparing…" : "Download my records"}</button>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      {data === null ? <div className="notice">{error ? "Could not load your records." : "Loading…"}</div> : (
        <>
          <div className="grid stats">
            <StatCard icon="services" label="Visit notes" value={data.counts.notes} note="signed by your doctor" />
            <StatCard icon="reports" label="Results" value={data.counts.results} note={`${data.counts.values_ready} with values to see`} />
            <StatCard icon="shield" label="Records held by clinics" value={data.counts.references} note="in their own systems" />
          </div>
          <div className="grid">
            <Card title={`Results (${data.results.length})`} sub="Tests and requests your clinic sent, once a result came back."
              action={<button className="btn ghost" onClick={() => goTo("requests")}>All tests and requests</button>}>
              {data.results.length === 0 ? <div className="empty">No results yet.</div> : data.results.map((r) => (
                <div key={r.id} className="appt">
                  <div className="info">
                    <b>{r.label} <Pill kind={r.state === "reviewed" ? "ok" : "warn"}>{r.state === "reviewed" ? "Reviewed by your doctor" : "Waiting for your doctor"}</Pill></b>
                    <small>{REQUEST[r.kind] ?? r.kind} · {r.partner}{r.result_at ? ` · result ${new Date(r.result_at).toLocaleDateString()}` : ""}</small>
                    {r.values_ready ? <Values id={r.id} /> : <small>{r.state === "reviewed" ? "The values will appear here a day after the review." : "Values appear after your doctor has reviewed the result."}</small>}
                  </div>
                </div>
              ))}
            </Card>
            <Card title={`Visit notes (${data.notes.length})`} action={<button className="btn ghost" onClick={() => goTo("notes")}>Read my notes</button>}>
              {!data.notes_enabled ? <div className="empty">Visit notes are not available here yet.</div>
                : data.notes.length === 0 ? <div className="empty">No notes to show yet.</div>
                : data.notes.map((n) => (
                  <div key={n.id} className="appt"><div className="info">
                    <b>Visit on {n.note_date} {n.corrected && <Pill kind="info">Corrected later</Pill>}</b>
                    <small>{n.author_name || "Your doctor"}{n.for_name ? ` · about ${n.for_name}` : ""}</small>
                  </div></div>
                ))}
            </Card>
            <Card title="Records held by clinics" sub="Zoiko Access keeps only where they are. Ask the clinic for a copy.">
              {refs === null ? <div className="empty">This list is not switched on for your clinic yet.</div>
                : refs.length === 0 ? <div className="empty">No clinic has listed a record for you yet.</div>
                : refs.map((r) => (
                  <div key={r.id} className="appt">
                    <div className="info">
                      <b>{KIND[r.kind] ?? r.kind} <small>dated {r.refers_to}</small></b>
                      <small>Held by {r.organization.name || "your clinic"} in its system “{r.system}”.</small>
                    </div>
                    {r.status === "withdrawn" ? <Pill kind="warn">No longer listed as held</Pill> : <Pill kind="ok">Held</Pill>}
                  </div>
                ))}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
