// Two-step sign-in screens (DEV-380), shared by the staff apps: the "Two-step sign-in" card on My
// account, the set-up a required login sees right after its password, and the code form of sign-in.
import { useCallback, useEffect, useState } from "react";
import type { TwoStepApi, TwoStepStatus } from "./twoStepApi";
import { Card, Pill } from "./ui";

const groups = (secret: string) => secret.match(/.{1,4}/g)?.join(" ") ?? secret;

/** The ten recovery codes, shown once. The person must say they saved them. */
function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div role="region" aria-label="Recovery codes">
      <div className="notice" role="status">
        Save these recovery codes somewhere safe, away from your phone. Each one signs you in once if you lose
        your phone. <b>They are shown only now.</b>
      </div>
      <ol style={{ fontFamily: "ui-monospace, monospace", columns: 2, margin: "12px 0" }}>
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ol>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn ghost" type="button" onClick={() => {
          void navigator.clipboard?.writeText(codes.join("\n")).then(() => setCopied(true)).catch(() => setCopied(false));
        }}>{copied ? "Copied" : "Copy codes"}</button>
        <button className="btn" type="button" onClick={onDone}>I have saved them</button>
      </div>
    </div>
  );
}

/** Off: the password, then the secret to add to an app, then its first code. */
function SetUp({ api, onOn }: { api: TwoStepApi; onOn: (codes: string[]) => void }) {
  const [password, setPassword] = useState("");
  const [made, setMade] = useState<{ secret: string; link: string } | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(step: () => Promise<void>) {
    setBusy(true); setError("");
    try { await step(); } catch (e) { setError(e instanceof Error ? e.message : "Something went wrong"); }
    finally { setBusy(false); }
  }

  if (!made) {
    return (
      <form onSubmit={(e) => { e.preventDefault(); void run(async () => { setMade(await api.start(password)); setPassword(""); }); }}
        style={{ display: "grid", gap: 8, maxWidth: 380 }}>
        <p>You will need an authenticator app on your phone (for example Google Authenticator, Microsoft
          Authenticator or 1Password). After your password, sign-in will ask for the 6-digit code it shows.</p>
        <input type="password" className="field" autoComplete="current-password" aria-label="Your password"
          placeholder="Your password" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <div className="error" role="alert">{error}</div>}
        <div><button className="btn" type="submit" disabled={busy || !password}>{busy ? "Checking…" : "Set up two-step sign-in"}</button></div>
      </form>
    );
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); void run(async () => onOn(await api.confirm(code.trim()))); }}
      style={{ display: "grid", gap: 8, maxWidth: 420 }}>
      <p><b>1.</b> In your authenticator app, add an account and type this setup key
        (or, on this device, <a href={made.link}>open it in your app</a>):</p>
      <p aria-label="Setup key" style={{ fontFamily: "ui-monospace, monospace", fontSize: 18, letterSpacing: 1, wordBreak: "break-all" }}>
        {groups(made.secret)}
      </p>
      <p><b>2.</b> Type the 6-digit code the app now shows:</p>
      <input className="field" inputMode="numeric" autoComplete="one-time-code" aria-label="Code from the app"
        placeholder="123456" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn" type="submit" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Turn on"}</button></div>
    </form>
  );
}

/** On: how many recovery codes are left, new ones, and switching it off (unless required). */
function Manage({ api, state, onChange }: { api: TwoStepApi; state: TwoStepStatus; onChange: () => void }) {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [off, setOff] = useState(false);

  async function run(step: () => Promise<void>) {
    setBusy(true); setError("");
    try { await step(); setCode(""); setPassword(""); } catch (e) { setError(e instanceof Error ? e.message : "Something went wrong"); }
    finally { setBusy(false); }
  }

  if (codes) return <RecoveryCodes codes={codes} onDone={() => { setCodes(null); onChange(); }} />;
  return (
    <div style={{ display: "grid", gap: 8, maxWidth: 380 }}>
      <p>On since {state.on_since ? new Date(state.on_since).toLocaleDateString() : "—"}.{" "}
        {state.recovery_codes_left} recovery code{state.recovery_codes_left === 1 ? "" : "s"} left.</p>
      {state.recovery_codes_left <= 2 && <div className="notice" role="status">Few recovery codes left: make new ones.</div>}
      <input className="field" inputMode="numeric" autoComplete="one-time-code" aria-label="Code from the app"
        placeholder="Code from your app" value={code} onChange={(e) => setCode(e.target.value)} />
      {off && (
        <input type="password" className="field" autoComplete="current-password" aria-label="Your password"
          placeholder="Your password" value={password} onChange={(e) => setPassword(e.target.value)} />
      )}
      {error && <div className="error" role="alert">{error}</div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {!off && <button className="btn" type="button" disabled={busy || !code.trim()}
          onClick={() => void run(async () => setCodes(await api.newCodes(code.trim())))}>New recovery codes</button>}
        {!state.required && !off && <button className="btn ghost" type="button" onClick={() => setOff(true)}>Turn off…</button>}
        {off && <>
          <button className="btn" type="button" disabled={busy || !code.trim() || !password}
            onClick={() => void run(async () => { await api.off(password, code.trim()); setOff(false); onChange(); })}>Turn off two-step sign-in</button>
          <button className="btn ghost" type="button" onClick={() => setOff(false)}>Keep it on</button>
        </>}
      </div>
    </div>
  );
}

/** The "Two-step sign-in" card. `onSetUp` is told once it is on and the codes are saved. */
export function TwoStepCard({ api, onSetUp }: { api: TwoStepApi; onSetUp?: () => void }) {
  const [state, setState] = useState<TwoStepStatus | null>(null);
  const [error, setError] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const load = useCallback(() => {
    void api.status().then((s) => { setState(s); setError(""); }).catch((e: Error) => setError(e.message));
  }, [api]);
  useEffect(load, [load]);

  let body;
  if (error) body = <div className="error" role="alert">{error}</div>;
  else if (!state) body = <p>Loading…</p>;
  else if (!state.available) body = <p>A development link has no password login. Sign in with your login and password to set this up.</p>;
  else if (codes) body = <RecoveryCodes codes={codes} onDone={() => { setCodes(null); load(); onSetUp?.(); }} />;
  else if (state.on) body = <Manage api={api} state={state} onChange={load} />;
  else body = <SetUp api={api} onOn={setCodes} />;

  return (
    <Card title="Two-step sign-in"
      sub="After your password, a code from an app on your phone: a stolen password alone is then not enough.">
      {state && (
        <p>
          <Pill kind={state.on ? "ok" : state.required ? "warn" : "info"}>{state.on ? "On" : "Off"}</Pill>{" "}
          {state.required && <Pill kind="info">Required by your organization</Pill>}
        </p>
      )}
      {body}
      {state?.on && <p className="muted" style={{ fontSize: 13 }}>
        For sensitive actions (team changes, merging records) you are asked for a code again if your last one is
        more than {state.recheck_minutes} minutes old.</p>}
    </Card>
  );
}

/** Right after the password, when the organization requires two-step and it is not set up yet. */
export function RequiredSetUp({ api, onDone, onCancel }: { api: TwoStepApi; onDone: () => void; onCancel: () => void }) {
  return (
    <div className="auth-card">
      <h2>Set up two-step sign-in</h2>
      <p className="lead">Your organization requires it for every staff login. It takes about two minutes.</p>
      <TwoStepCard api={api} onSetUp={onDone} />
      <p className="switch"><button className="btn ghost" type="button" onClick={onCancel}>Sign out</button></p>
    </div>
  );
}

/** The code form of sign-in, after a right password. */
export function SecondStepForm({ onCode, onBack, error, busy }: {
  onCode: (code: string) => void; onBack: () => void; error: string; busy: boolean;
}) {
  const [code, setCode] = useState("");
  return (
    <form onSubmit={(e) => { e.preventDefault(); onCode(code.trim()); setCode(""); }}>
      <label htmlFor="two-step-code">Code from your authenticator app</label>
      <input id="two-step-code" className="field" inputMode="numeric" autoComplete="one-time-code" autoFocus
        value={code} onChange={(e) => setCode(e.target.value)} required />
      <p className="muted" style={{ fontSize: 13 }}>Lost your phone? Type one of your recovery codes instead.</p>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn wide" type="submit" disabled={busy || !code.trim()}>{busy ? "Checking…" : "Continue"}</button>
      <button className="btn ghost wide" type="button" onClick={onBack}>Back</button>
    </form>
  );
}
