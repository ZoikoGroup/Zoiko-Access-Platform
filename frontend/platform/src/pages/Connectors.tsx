import { useCallback, useEffect, useState } from "react";
import { connectorStep, getConnectors, type ConnectorCert } from "../api";
import { Card, Pill } from "../ui";

type PillKind = "ok" | "warn" | "bad" | "info";
const STAGE: Record<ConnectorCert["stage"], { text: string; kind: PillKind }> = {
  sandbox: { text: "In the sandbox", kind: "info" },
  certified: { text: "Certified, not live", kind: "warn" },
  live: { text: "Live", kind: "ok" },
  suspended: { text: "Suspended", kind: "bad" },
};

const SOURCE: Record<string, string> = {
  built_in: "Part of the product.",
  plugin: "Plugged in: adapter code named in this server's settings.",
  http: "Plugged in: a partner speaking Zoiko's JSON contract, set up in this server's settings.",
};

/** Partner systems and their certification (DEV-230). Three different people: support enrols,
 * a safety officer certifies, another support officer puts it live. A new version starts over. */
export function Connectors() {
  const [data, setData] = useState<Awaited<ReturnType<typeof getConnectors>> | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(async () => {
    try { setData(await getConnectors()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load partner systems"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function act(key: string, step: "enrol" | "certify" | "live" | "suspend") {
    setError("");
    try { await connectorStep(key, step, step === "suspend" ? { note } : {}); setNote(""); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
  }
  return (
    <>
      <div className="hero"><div>
        <h1>Partner systems</h1>
        <p>No clinic can use a partner's system until this version of its interface is certified and live. If the partner changes its interface, the new version starts again in the sandbox.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {(data?.not_loaded ?? []).length > 0 && (
        <div className="error" role="alert">
          <b>Partner systems named in this server's settings that did not load:</b>
          <ul>{data!.not_loaded!.map((p) => <li key={p.name}>{p.name}: {p.reason}</li>)}</ul>
        </div>
      )}
      <p><small>A partner is added by this server's settings, not on this page: adapter code (ZOIKO_CONNECTOR_PLUGINS), or a partner speaking Zoiko's JSON contract (ZOIKO_CONNECTOR_HTTP). Each then starts in the sandbox below.</small></p>
      <div className="grid">
        {(data?.connectors ?? []).map((c) => {
          const cert = c.certification;
          return (
            <Card key={c.connector_key} title={`${c.connector_key} (version ${c.profile_version})`}
              sub={c.simulated ? "Pretend partner for development: synthetic data only, needs no certificate."
                : SOURCE[c.source ?? "built_in"]}>
              {!c.simulated && (
                <>
                  <p>{cert ? <Pill kind={STAGE[cert.stage].kind}>{STAGE[cert.stage].text}</Pill> : <Pill kind="info">Not enrolled</Pill>}</p>
                  {cert && <small>Enrolled by {cert.enrolled_by}{cert.certified_by && `; certified by ${cert.certified_by}`}{cert.live_by && `; put live by ${cert.live_by}`}.{cert.note && ` Note: ${cert.note}`}</small>}
                  <div className="form-row">
                    {!cert && data?.can_write && <button className="btn" onClick={() => void act(c.connector_key, "enrol")}>Enrol in the sandbox</button>}
                    {cert?.stage === "sandbox" && data?.can_attest && <button className="btn" onClick={() => void act(c.connector_key, "certify")}>Certify</button>}
                    {cert?.stage === "certified" && data?.can_write && <button className="btn" onClick={() => void act(c.connector_key, "live")}>Put live</button>}
                    {cert && cert.stage !== "suspended" && (
                      <>
                        {" "}<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reason to suspend" aria-label={`Reason to suspend ${c.connector_key}`} />
                        {" "}<button className="btn ghost" disabled={note.trim().length < 3} onClick={() => void act(c.connector_key, "suspend")}>Suspend</button>
                      </>
                    )}
                  </div>
                </>
              )}
            </Card>
          );
        })}
      </div>
    </>
  );
}
