import { useCallback, useEffect, useState } from "react";
import { ApiError, getPacks, type PackRow, type PacksData } from "../api";
import { Card, Pill } from "../ui";

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "Your account may not see the country packs."
    : e instanceof Error ? e.message : "Could not load";
}

const day = (iso: string) => new Date(iso).toLocaleDateString();

/** Why an installed pack is not in force right now, in plain words. */
function why(p: PackRow, floor: number): string {
  if (p.status === "revoked") return "Revoked: a signed notice raised the revocation floor above it.";
  if (p.status === "superseded") return "Replaced by a newer version.";
  if (p.revocation_epoch < floor) return "Below the revocation floor.";
  if (new Date(p.expires_at) <= new Date()) return "Expired.";
  if (new Date(p.valid_from) > new Date()) return "Not valid yet.";
  return "Its signature no longer checks against a key this cell trusts.";
}

/** Which countries have which features switched on. Read only: a pack is installed by a command and
 *  is checked again on every use, so a listed pack that has expired or been revoked switches nothing on. */
export function Packs() {
  const [data, setData] = useState<PacksData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Every view is written to the platform audit log, so load on open and on request: no polling.
  const load = useCallback(async () => {
    setBusy(true);
    try { setData(await getPacks()); setError(""); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <>
      <div className="hero">
        <div>
          <h1>Country packs</h1>
          <p>A pack is a signed file that says which features a country may use. Nothing is on unless a pack for that
            country is in force right now. This page only shows them: packs are installed by a command on the server
            and cannot be changed from here.</p>
        </div>
        <span className="spacer" />
        <button className="btn" onClick={() => void load()} disabled={busy}>Refresh</button>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      {data === null ? (!error && <div className="notice">Loading…</div>) : (
        <div className="grid">
          <Card title="What this cell trusts" sub="Key names only: the keys themselves are never shown.">
            <p>Revocation floor: <b>{data.revocation_floor}</b>. A pack issued below this number never counts.</p>
            <p>Signing keys accepted: {data.trusted_key_ids.length === 0
              ? <b>none, so no pack can be installed and every feature stays off.</b>
              : <b>{data.trusted_key_ids.join(", ")}</b>}</p>
          </Card>
          <Card title={`Installed packs (${data.packs.length})`} sub="Newest first.">
            {data.packs.length === 0 ? <div className="empty">No pack is installed, so nothing is switched on in any country.</div> : data.packs.map((p) => (
              <div key={p.public_id} className="appt">
                <div className="info">
                  <b>{p.country} version {p.version}{" "}
                    <Pill kind={p.in_force ? "ok" : "bad"}>{p.in_force ? "In force now" : "Not in force"}</Pill></b>
                  <small>Valid {day(p.valid_from)} to {day(p.expires_at)}. Signed with key {p.key_id}. Revocation number {p.revocation_epoch}.</small>
                  <small>{p.in_force
                    ? (p.capabilities.length === 0 ? "Switches nothing on." : `Switched on: ${p.capabilities.join(", ")}.`)
                    : why(p, data.revocation_floor)}</small>
                </div>
              </div>
            ))}
          </Card>
        </div>
      )}
    </>
  );
}
