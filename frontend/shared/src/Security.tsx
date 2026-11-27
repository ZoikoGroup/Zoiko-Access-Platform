import { useCallback, useEffect, useState } from "react";
import { Card, Pill } from "./ui";

/** How an app calls the API as the signed-in person: GET without a body, POST with one. */
export type SecurityCall = <T>(path: string, body?: unknown) => Promise<T>;

const codeOf = (e: unknown) => (e as { code?: string } | null)?.code ?? "";
function why(e: unknown, wrong: string): string {
  const code = codeOf(e);
  return code === "VALIDATION_FAILED" ? wrong
    : code === "CONFLICT_VERSION" ? "That changed meanwhile. Reload the page and try again."
    : code === "NOT_FOUND" ? "This login has no password of its own, so there is nothing to protect here."
    : e instanceof Error ? e.message : "That did not work";
}

interface Status { enabled: boolean; recovery_left: number }

function TwoStep({ call }: { call: SecurityCall }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [setup, setSetup] = useState<{ secret: string; otpauth_url: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setStatus(await call<Status>("auth/second-factor")); }
    catch (e) { setError(why(e, "Could not load")); }
  }, [call]);
  useEffect(() => { void load(); }, [load]);

  async function run(work: () => Promise<void>, wrong: string) {
    setBusy(true); setError("");
    try { await work(); }
    catch (e) { setError(why(e, wrong)); }
    finally { setBusy(false); }
  }
  const begin = () => run(async () => {
    setSetup(await call("auth/second-factor/start", { password }));
    setPassword("");
  }, "That password is not right.");
  const confirm = () => run(async () => {
    const done = await call<{ recovery_codes: string[] }>("auth/second-factor/confirm", { code });
    setCodes(done.recovery_codes); setSetup(null); setCode("");
    setStatus({ enabled: true, recovery_left: done.recovery_codes.length });
  }, "That code is not right. Check the key was typed correctly, and use the newest code.");
  const off = () => run(async () => {
    await call("auth/second-factor/disable", { password, code });
    setPassword(""); setCode(""); await load();
  }, "Check your password and code. Codes work once.");

  return (
    <Card title="Two-step sign-in" sub="After your password you also type a code from an app on your phone, so a stolen password is not enough.">
      {error && <div className="error" role="alert">{error}</div>}
      {status === null ? <div className="empty">{error ? "Could not load." : "Loading…"}</div> : (
        <>
          <p><Pill kind={status.enabled ? "ok" : "info"}>{status.enabled ? "On" : "Off"}</Pill>{status.enabled ? ` ${status.recovery_left} recovery codes left.` : ""}</p>
          {status.enabled && <small>Lost your phone and your recovery codes? Your clinic (or, for staff, your clinic administrator) can turn it off after checking who you are, so you can set it up again.</small>}
          {codes && (
            <div className="notice" role="status">
              <b>Save these recovery codes now. They are not shown again.</b> Each one signs you in once if you do not have your phone.
              <ul className="recovery">{codes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
              <button type="button" className="btn" onClick={() => setCodes(null)}>I have saved them</button>
            </div>
          )}
          {!status.enabled && !setup && (
            <form className="profile-form" onSubmit={(e) => { e.preventDefault(); void begin(); }}>
              <label>Your password
                <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
              <button className="btn" type="submit" disabled={busy || !password}>Set up two-step sign-in</button>
            </form>
          )}
          {!status.enabled && setup && (
            <form className="profile-form" onSubmit={(e) => { e.preventDefault(); void confirm(); }}>
              <p>In your authenticator app, add an account and type this key:</p>
              <p><code className="secret-key">{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code></p>
              <p><a href={setup.otpauth_url}>Or open it in an authenticator on this device</a></p>
              <label>The six digits it now shows
                <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.trim())} />
              </label>
              <div className="row">
                <button className="btn" type="submit" disabled={busy || code.length !== 6}>Turn it on</button>
                <button className="btn ghost" type="button" onClick={() => { setSetup(null); setCode(""); }}>Cancel</button>
              </div>
            </form>
          )}
          {status.enabled && !codes && (
            <form className="profile-form" onSubmit={(e) => { e.preventDefault(); void off(); }}>
              <label>Your password
                <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
              <label>A code from your authenticator, or a recovery code
                <input autoComplete="one-time-code" maxLength={32} value={code} onChange={(e) => setCode(e.target.value.trim())} />
              </label>
              <button className="btn ghost" type="submit" disabled={busy || !password || !code}>Turn two-step sign-in off</button>
            </form>
          )}
        </>
      )}
    </Card>
  );
}

interface Identity { id: string; provider_name: string; email: string; linked_at: string; unlinked_at: string | null }

function OutsideSignIns({ call }: { call: SecurityCall }) {
  const [rows, setRows] = useState<Identity[] | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setRows((await call<{ identities: Identity[] }>("auth/identities")).identities); }
    catch (e) { setError(why(e, "Could not load")); }
  }, [call]);
  useEffect(() => { void load(); }, [load]);
  async function change(id: string, step: "unlink" | "allow") {
    setBusy(true); setError("");
    try { await call(`auth/identities/${encodeURIComponent(id)}/${step}`, { password }); setPassword(""); await load(); }
    catch (e) { setError(why(e, "That password is not right.")); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Sign-in with other providers" sub="Google, or your organization's own sign-in, once you have used it here.">
      {error && <div className="error" role="alert">{error}</div>}
      {rows === null ? <div className="empty">{error ? "Could not load." : "Loading…"}</div>
        : rows.length === 0 ? <div className="empty">No other sign-in is linked to this login.</div> : (
          <>
            <div className="profile-form">
              <label>Your password (needed to unlink or allow)
                <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
            </div>
            {rows.map((r) => (
              <div key={r.id} className="appt">
                <div className="info">
                  <b>{r.provider_name} <Pill kind={r.unlinked_at ? "warn" : "ok"}>{r.unlinked_at ? "unlinked" : "linked"}</Pill></b>
                  <small>{r.email || "no email"} · first used {new Date(r.linked_at).toLocaleDateString()}</small>
                </div>
                <button className="btn ghost" disabled={busy || !password} onClick={() => void change(r.id, r.unlinked_at ? "allow" : "unlink")}>
                  {r.unlinked_at ? "Allow again" : "Unlink"}<span className="sr-only"> {r.provider_name}</span>
                </button>
              </div>
            ))}
          </>
        )}
    </Card>
  );
}

/** The account page's security cards, shared by the five apps. */
export function SecurityCards({ call }: { call: SecurityCall }) {
  return (
    <>
      <TwoStep call={call} />
      <OutsideSignIns call={call} />
    </>
  );
}
