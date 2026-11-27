// Two-step sign-in screens (DEV-380), shared by the staff apps: the "Two-step sign-in" card on My
// account, the set-up a required login sees right after its password, and the code form of sign-in.
import { useCallback, useEffect, useState } from "react";
import type { TwoStepApi, TwoStepStatus } from "./twoStepApi";
import { formatDate, t } from "./i18n";
import { Card, Pill } from "./ui";

const groups = (secret: string) => secret.match(/.{1,4}/g)?.join(" ") ?? secret;

/** The ten recovery codes, shown once. The person must say they saved them. */
function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div role="region" aria-label={t("twoStep.recoveryCodes")}>
      <div className="notice" role="status">
        {t("twoStep.saveCodes")}{" "}
        <b>{t("twoStep.shownOnce")}</b>
      </div>
      <ol style={{ fontFamily: "ui-monospace, monospace", columns: 2, margin: "12px 0" }}>
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ol>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn ghost" type="button" onClick={() => {
          void navigator.clipboard?.writeText(codes.join("\n")).then(() => setCopied(true)).catch(() => setCopied(false));
        }}>{copied ? t("common.copied") : t("twoStep.copyCodes")}</button>
        <button className="btn" type="button" onClick={onDone}>{t("twoStep.saved")}</button>
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
    try { await step(); } catch (e) { setError(e instanceof Error ? e.message : t("common.somethingWrong")); }
    finally { setBusy(false); }
  }

  if (!made) {
    return (
      <form onSubmit={(e) => { e.preventDefault(); void run(async () => { setMade(await api.start(password)); setPassword(""); }); }}
        style={{ display: "grid", gap: 8, maxWidth: 380 }}>
        <p>{t("twoStep.needApp")}</p>
        <input type="password" className="field" autoComplete="current-password" aria-label={t("common.yourPassword")}
          placeholder={t("common.yourPassword")} value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <div className="error" role="alert">{error}</div>}
        <div><button className="btn" type="submit" disabled={busy || !password}>{busy ? t("common.checking") : t("twoStep.setUp")}</button></div>
      </form>
    );
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); void run(async () => onOn(await api.confirm(code.trim()))); }}
      style={{ display: "grid", gap: 8, maxWidth: 420 }}>
      <p><b>1.</b> {t("twoStep.step1")}
        ({t("twoStep.orOnDevice")} <a href={made.link}>{t("twoStep.openInApp")}</a>):</p>
      <p aria-label={t("twoStep.setupKey")} style={{ fontFamily: "ui-monospace, monospace", fontSize: 18, letterSpacing: 1, wordBreak: "break-all" }}>
        {groups(made.secret)}
      </p>
      <p><b>2.</b> {t("twoStep.step2")}</p>
      <input className="field" inputMode="numeric" autoComplete="one-time-code" aria-label={t("twoStep.codeFromApp")}
        placeholder="123456" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn" type="submit" disabled={busy || code.length !== 6}>{busy ? t("common.checking") : t("twoStep.turnOn")}</button></div>
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
    try { await step(); setCode(""); setPassword(""); } catch (e) { setError(e instanceof Error ? e.message : t("common.somethingWrong")); }
    finally { setBusy(false); }
  }

  if (codes) return <RecoveryCodes codes={codes} onDone={() => { setCodes(null); onChange(); }} />;
  return (
    <div style={{ display: "grid", gap: 8, maxWidth: 380 }}>
      <p>{t("twoStep.onSince", { date: state.on_since ? formatDate(state.on_since) : "—" })}{" "}
        {t(state.recovery_codes_left === 1 ? "twoStep.codeLeft" : "twoStep.codesLeft", { count: state.recovery_codes_left })}</p>
      {state.recovery_codes_left <= 2 && <div className="notice" role="status">{t("twoStep.fewLeft")}</div>}
      <input className="field" inputMode="numeric" autoComplete="one-time-code" aria-label={t("twoStep.codeFromApp")}
        placeholder={t("twoStep.codeFromYourApp")} value={code} onChange={(e) => setCode(e.target.value)} />
      {off && (
        <input type="password" className="field" autoComplete="current-password" aria-label={t("common.yourPassword")}
          placeholder={t("common.yourPassword")} value={password} onChange={(e) => setPassword(e.target.value)} />
      )}
      {error && <div className="error" role="alert">{error}</div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {!off && <button className="btn" type="button" disabled={busy || !code.trim()}
          onClick={() => void run(async () => setCodes(await api.newCodes(code.trim())))}>{t("twoStep.newCodes")}</button>}
        {!state.required && !off && <button className="btn ghost" type="button" onClick={() => setOff(true)}>{t("twoStep.turnOffStart")}</button>}
        {off && <>
          <button className="btn" type="button" disabled={busy || !code.trim() || !password}
            onClick={() => void run(async () => { await api.off(password, code.trim()); setOff(false); onChange(); })}>{t("twoStep.turnOff")}</button>
          <button className="btn ghost" type="button" onClick={() => setOff(false)}>{t("twoStep.keepOn")}</button>
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
  else if (!state) body = <p>{t("common.loading")}</p>;
  else if (!state.available) body = <p>{t("twoStep.devLink")}</p>;
  else if (codes) body = <RecoveryCodes codes={codes} onDone={() => { setCodes(null); load(); onSetUp?.(); }} />;
  else if (state.on) body = <Manage api={api} state={state} onChange={load} />;
  else body = <SetUp api={api} onOn={setCodes} />;

  return (
    <Card title={t("auth.twoStepTitle")}
      sub={t("twoStep.sub")}>
      {state && (
        <p>
          <Pill kind={state.on ? "ok" : state.required ? "warn" : "info"}>{state.on ? t("common.on") : t("common.off")}</Pill>{" "}
          {state.required && <Pill kind="info">{t("twoStep.required")}</Pill>}
        </p>
      )}
      {body}
      {state?.on && <p className="muted" style={{ fontSize: 13 }}>
        {t("twoStep.recheck", { minutes: state.recheck_minutes })}</p>}
    </Card>
  );
}

/** Right after the password, when the organization requires two-step and it is not set up yet. */
export function RequiredSetUp({ api, onDone, onCancel }: { api: TwoStepApi; onDone: () => void; onCancel: () => void }) {
  return (
    <div className="auth-card">
      <h2>{t("twoStep.setUp")}</h2>
      <p className="lead">{t("twoStep.requiredLead")}</p>
      <TwoStepCard api={api} onSetUp={onDone} />
      <p className="switch"><button className="btn ghost" type="button" onClick={onCancel}>{t("common.signOut")}</button></p>
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
      <label htmlFor="two-step-code">{t("twoStep.codeLabel")}</label>
      <input id="two-step-code" className="field" inputMode="numeric" autoComplete="one-time-code" autoFocus
        value={code} onChange={(e) => setCode(e.target.value)} required />
      <p className="muted" style={{ fontSize: 13 }}>{t("twoStep.lostPhone")}</p>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn wide" type="submit" disabled={busy || !code.trim()}>{busy ? t("common.checking") : t("common.continue")}</button>
      <button className="btn ghost wide" type="button" onClick={onBack}>{t("common.back")}</button>
    </form>
  );
}
