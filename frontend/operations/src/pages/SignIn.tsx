import { useState } from "react";
import { AuthShell, GoogleButton, PasswordField } from "../AuthShell";
import { setSession, signIn, type Session } from "../api";

// Where each kind of person belongs. Only the patient app exists besides this one.
const OTHER_APPS: Record<string, { name: string; url: string }> = {
  patient: { name: "the patient app", url: "http://localhost:5174/" },
  platform: { name: "the platform console", url: "http://localhost:5175/" },
  clinician: { name: "the doctor app", url: "http://localhost:5176/" },
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
      if (reply.home !== "operations") {
        setElsewhere(reply.home);            // right password, wrong app: do not keep the token
        setPassword("");
        return;
      }
      const session: Session = {
        token: reply.token, tenantId: reply.tenant.id, tenantName: reply.tenant.name,
        login: reply.actor.login, roles: reply.roles,
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
    <AuthShell headline="Run your clinic's day with confidence."
      points={["Manage services and open times", "Confirm and track bookings", "Every action is recorded for audit"]}>
      <div className="auth-card">
        <h2>Staff sign in</h2>
        <p className="lead">For clinic operations, support, safety and integration staff.</p>

        <GoogleButton />
        <div className="divider">or use your work login</div>

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

        <p className="switch">Staff accounts are created by your organization's administrator. Patients: use the patient app.</p>
      </div>
    </AuthShell>
  );
}
