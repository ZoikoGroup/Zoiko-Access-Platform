import { useEffect, useRef, useState } from "react";
import { AuthShell, PasswordField } from "../AuthShell";
import { resetPassword, verifyEmail } from "../api";

export interface LinkAction { action: "verify" | "reset"; token: string }

/** Reads ?action=verify|reset&token=... from the address bar (the links we email). */
export function readLinkAction(): LinkAction | null {
  const q = new URLSearchParams(window.location.search);
  const action = q.get("action");
  const token = q.get("token");
  return token && (action === "verify" || action === "reset") ? { action, token } : null;
}

export function clearLink(): void {
  window.history.replaceState({}, "", window.location.pathname);
}

const POINTS = ["Manage services and open times", "Confirm and track bookings", "Every action is recorded for audit"];

/** The page a person lands on after clicking a link in an email. */
export function LinkPage({ link, onDone }: { link: LinkAction; onDone: () => void }) {
  return (
    <AuthShell headline="Run your clinic's day with confidence." points={POINTS}>
      <div className="auth-card">
        {link.action === "verify" ? <Verify token={link.token} onDone={onDone} />
          : <Reset token={link.token} onDone={onDone} />}
      </div>
    </AuthShell>
  );
}

function Verify({ token, onDone }: { token: string; onDone: () => void }) {
  const [state, setState] = useState<"working" | "ok" | "bad">("working");
  const started = useRef(false);        // a link works once: never call twice (React dev double-run)

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    verifyEmail(token).then((ok) => setState(ok ? "ok" : "bad"));
  }, [token]);

  return (
    <>
      <h2>{state === "ok" ? "Email verified" : state === "bad" ? "This link did not work" : "Verifying…"}</h2>
      <p className="lead">
        {state === "ok" && "Thank you. Your email address is confirmed."}
        {state === "bad" && "The link may have expired or already been used. Ask your administrator to send a new one."}
        {state === "working" && "One moment."}
      </p>
      {state !== "working" && <button className="btn wide" onClick={onDone}>Continue to Zoiko Access</button>}
    </>
  );
}

function Reset({ token, onDone }: { token: string; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== confirm) { setError("The two passwords do not match."); return; }
    setBusy(true);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the password");
    } finally { setBusy(false); }
  }

  if (done) {
    return (
      <>
        <h2>Password changed</h2>
        <p className="lead">You have been signed out everywhere. Sign in with your new password.</p>
        <button className="btn wide" onClick={onDone}>Go to sign in</button>
      </>
    );
  }
  return (
    <form onSubmit={(e) => void submit(e)}>
      <h2>Choose a new password</h2>
      <p className="lead">A long password that is not your email.</p>
      <PasswordField id="new-password" label="New password" value={password} onChange={setPassword} autoComplete="new-password" />
      <PasswordField id="confirm-password" label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn wide" type="submit" disabled={busy || !password || !confirm}>
        {busy ? "Saving…" : "Set new password"}
      </button>
    </form>
  );
}
