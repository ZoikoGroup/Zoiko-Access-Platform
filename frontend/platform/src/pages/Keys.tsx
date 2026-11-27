import { useCallback, useEffect, useState } from "react";
import { ApiError, getKeys, type KeysData } from "../api";
import { Card, Pill } from "../ui";

const WHERE: Record<string, string> = {
  env: "An environment variable (development only)",
  file: "A key file that only the service can read",
  wrapped: "A key file sealed by a managed key service",
};

function friendly(e: unknown): string {
  const code = e instanceof ApiError ? e.code : "";
  return code === "POLICY_DENIED" ? "Your account may not see the key status."
    : e instanceof Error ? e.message : "Could not load";
}

/** Where this cell's encryption keys come from and which one is current: a short fingerprint of each,
 *  never a key. Read only: keys are made, wrapped, split and rotated by a command on the server. */
export function Keys() {
  const [data, setData] = useState<KeysData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Every view is written to the platform audit log, so load on open and on request: no polling.
  const load = useCallback(async () => {
    setBusy(true);
    try { setData(await getKeys()); setError(""); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <>
      <div className="hero">
        <div>
          <h1>Keys and sign-in</h1>
          <p>Health and identity details are stored only as ciphertext. This page shows where the keys come from
            and which one encrypts now. It never shows a key: only a short fingerprint to tell them apart.</p>
        </div>
        <span className="spacer" />
        <button className="btn ghost" disabled={busy} onClick={() => void load()}>Check again</button>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      {data && !data.ok && (
        <div className="error" role="alert">The key ring could not be loaded. Nothing can be encrypted or read until it is fixed.</div>
      )}
      <div className="grid">
        <Card title="Encryption keys" sub="One key per id; the current one encrypts, every listed one can still decrypt.">
          {!data ? <div className="empty">Loading…</div> : (
            <>
              <p>
                <Pill kind={data.provider === "env" ? "warn" : "ok"}>{data.provider}</Pill>{" "}
                {WHERE[data.provider] ?? "Unknown key store"}
              </p>
              <table className="table">
                <caption className="sr-only">The encryption keys</caption>
                <thead><tr><th>Key id</th><th>Fingerprint</th><th>Used to encrypt</th></tr></thead>
                <tbody>
                  {data.keys.map((k) => (
                    <tr key={k.id}>
                      <td>{k.id}</td><td><code>{k.fingerprint}</code></td>
                      <td>{k.current ? <Pill kind="ok">current</Pill> : "only decrypts"}</td>
                    </tr>
                  ))}
                  {data.keys.length === 0 && <tr><td colSpan={3} className="empty">No keys loaded.</td></tr>}
                </tbody>
              </table>
              {data.warnings.map((w) => <div key={w} className="notice" role="status">{w}</div>)}
              <p className="hint">To change keys, run <code>manage.py field_keys new</code>, then{" "}
                <code>manage.py rotate_field_keys</code>. To survive losing a key, run{" "}
                <code>manage.py field_keys split</code> and give the shares to different people.</p>
            </>
          )}
        </Card>
        <Card title="Outside sign-in" sub="Offered on the sign-in pages. Matched only by a verified email, never created from a provider.">
          {!data ? <div className="empty">Loading…</div>
            : data.sso.length === 0 ? <div className="empty">No provider is set up. People sign in with their login and password.</div>
            : <ul>{data.sso.map((p) => <li key={p.key}>{p.name} <small>({p.key})</small></li>)}</ul>}
        </Card>
      </div>
    </>
  );
}
