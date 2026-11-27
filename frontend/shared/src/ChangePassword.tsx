import { useState } from "react";
import { Card } from "./ui";

/** The plain-words error for a failed change-password call, whichever app's ApiError it is (the
 *  classes differ per app, so it reads the error's `code` field). */
export function friendlyPasswordError(e: unknown): Error {
  const code = (e as { code?: string } | null)?.code;
  if (code === "VALIDATION_FAILED") {
    return new Error("That did not work. Check your current password, and use a new password that is long enough and different from the old one.");
  }
  if (code === "RATE_LIMITED") return new Error("Too many tries. Wait a little and try again.");
  return e instanceof Error ? e : new Error("Could not change the password");
}

/** "Change my password", shared by the apps. The app supplies `onChange`, which calls its own API,
 *  swaps in the fresh session token and throws an Error with a plain message on failure. The
 *  server checks the current password and the new one's strength; this form only catches typing
 *  mistakes (empty fields, the two new passwords not matching) before asking. */
export function ChangePassword({ onChange }: { onChange: (current: string, next: string) => Promise<void> }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setDone(false);
    if (!current || !next) { setError("Fill in your current and your new password."); return; }
    if (next !== again) { setError("The two new passwords are not the same."); return; }
    if (next === current) { setError("Choose a password different from the current one."); return; }
    setBusy(true); setError("");
    try {
      await onChange(current, next);
      setCurrent(""); setNext(""); setAgain(""); setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Change my password"
      sub="Every other device is signed out when you change it. Use a long password you do not use anywhere else.">
      <form onSubmit={(e) => void submit(e)} style={{ display: "grid", gap: 8, maxWidth: 360 }}>
        <input type="password" autoComplete="current-password" aria-label="Current password" placeholder="Current password"
          value={current} onChange={(e) => setCurrent(e.target.value)} />
        <input type="password" autoComplete="new-password" aria-label="New password" placeholder="New password"
          value={next} onChange={(e) => setNext(e.target.value)} />
        <input type="password" autoComplete="new-password" aria-label="Repeat the new password" placeholder="Repeat the new password"
          value={again} onChange={(e) => setAgain(e.target.value)} />
        {error && <div className="error" role="alert">{error}</div>}
        {done && <div className="notice" role="status" style={{ background: "var(--ok-bg)", color: "var(--ok)" }}>Password changed. Other devices were signed out.</div>}
        <div><button className="btn" type="submit" disabled={busy}>{busy ? "Changing…" : "Change password"}</button></div>
      </form>
    </Card>
  );
}
