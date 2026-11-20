import { useEffect, useState } from "react";
import { confirmAlertPhone, getAlertPhone, setAlertPhone, setCriticalTexts, type AlertPhone as Info } from "../api";
import { Card, Pill } from "../ui";

/** A doctor's own mobile number for a text when a result they ordered comes back critical (DEV-334). */
export function AlertPhone(_: { admin: boolean }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [number, setNumber] = useState("");
  const [code, setCode] = useState("");
  const [asked, setAsked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { getAlertPhone().then(setInfo).catch(() => setInfo(null)); }, []);
  async function run(fn: () => Promise<Info>, after?: () => void) {
    setBusy(true); setError("");
    try { setInfo(await fn()); after?.(); }
    catch (e) {
      const c = (e as { code?: string }).code;
      setError(c === "VALIDATION_FAILED" ? "Check it: the number starts with + and the country code, and the code has 6 digits."
        : c === "RATE_LIMITED" ? "Three codes were sent this hour. Try again later."
        : c === "JURISDICTION_INACTIVE" ? "Texts are not switched on for your country yet."
        : c === "CONFLICT_VERSION" ? "Ask for a new code, or prove your number first."
        : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }
  if (!info) return null;
  return (
    <Card title="Urgent alerts by text">
      <p>Get a text when a result you ordered comes back critical. The text only says a result needs your attention: never what it is or whom it is about. It goes to your own mobile, once you have proved it with a code.</p>
      {info.number && <p>Your number: <b>{info.number}</b> <Pill kind={info.verified ? "ok" : "warn"}>{info.verified ? "proved" : "not proved yet"}</Pill></p>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input className="field" aria-label="Your mobile number" placeholder="+44 7700 900123" value={number} onChange={(e) => setNumber(e.target.value)} />
        <button className="btn ghost" disabled={busy || !number.trim()} onClick={() => void run(() => setAlertPhone(number), () => setAsked(true))}>{info.number ? "Change and send a code" : "Send me a code"}</button>
      </div>
      {(asked || (info.number && !info.verified)) && (
        <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
          <input className="field" aria-label="Code from the text" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.trim())} style={{ width: 120 }} />
          <button className="btn" disabled={busy || code.length !== 6} onClick={() => void run(() => confirmAlertPhone(code), () => { setAsked(false); setCode(""); })}>Prove my number</button>
        </div>
      )}
      <label style={{ display: "block", marginTop: 8 }}>
        <input type="checkbox" checked={info.critical_texts} disabled={busy || !info.verified} onChange={(e) => { const on = e.target.checked; void run(() => setCriticalTexts(on)); }} />{" "}
        Text me when a result I ordered is critical
      </label>
      {!info.texts_available && <small>Texts are not switched on for your country yet; the email alert still goes.</small>}
      {error && <div className="error" role="alert">{error}</div>}
    </Card>
  );
}
