import { useState } from "react";
import { AuthShell, GoogleButton, PasswordField } from "../AuthShell";
import { setSession, signIn, type Session } from "../api";

// Where each kind of person belongs.
const OTHER_APPS: Record<string, { name: string; url: string }> = {
  patient: { name: "the patient app", url: "http://localhost:5174/" },
  operations: { name: "the staff console", url: "http://localhost:5173/" },
  platform: { name: "the platform console", url: "http://localhost:5175/" },
};

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [organization, setOrganization] = useState("");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [elsewhere, setElsewhere] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setElsewhere(null);
    try {
      const reply = await signIn(organization, login, password);
      if (reply.home !== "clinician") {
        setElsewhere(reply.home);            // right password, wrong app: do not keep the token
        setPassword("");
        return;
      }
      const session: Session = {
        token: reply.token, tenantName: reply.tenant.name, login: reply.actor.login, roles: reply.roles,
      };
      setSession(session);
      onSignedIn(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setPassword("");
    } finally { setBusy(false); }
  }

  const other = elsewhere ? OTHER_APPS[elsewhere] : undefined;
  return (
    <AuthShell headline="Your patients and your day, in one place."
      points={["See your appointments as they are confirmed", "See only the patients you care for", "Every look at a patient is recorded"]}>
      <div className="auth-card">
        <h2>Doctor sign in</h2>
        <p className="lead">For doctors and clinicians.</p>

        <GoogleButton />
        <div className="divider">or use your login</div>

        <form onSubmit={(e) => void submit(e)}>
          <label htmlFor="org">Organization</label>
          <input id="org" className="field" value={organization} onChange={(e) => setOrganization(e.target.value)}
            autoComplete="organization" required />
          <label htmlFor="login">Login</label>
          <input id="login" className="field" value={login} onChange={(e) => setLogin(e.target.value)}
            autoComplete="username" required />
          <PasswordField id="password" label="Password" value={password} onChange={setPassword}
            autoComplete="current-password" />
          {error && <div className="error" role="alert">{error}</div>}
          {elsewhere && (
            <div className="notice" role="alert">
              {other ? <>This account belongs in {other.name}. <a href={other.url}>Open it</a>.</>
                : <>This account does not have an app yet (role area: {elsewhere}).</>}
            </div>
          )}
          <button className="btn wide" type="submit" disabled={busy || !organization || !login || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="switch">New here? Your administrator sends you an invitation email.</p>
      </div>
    </AuthShell>
  );
}
