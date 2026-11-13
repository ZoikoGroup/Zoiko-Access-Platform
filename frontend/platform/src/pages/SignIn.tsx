import { useEffect, useState } from "react";
import { readDevLogin } from "@shared/devLogin";
import { AuthShell, PasswordField, RememberRow, SsoButtons } from "../AuthShell";
import { setSession, signIn, type Session } from "../api";

// Where each kind of person belongs.
const OTHER_APPS: Record<string, { name: string; url: string }> = {
  partner: { name: "the partner app", url: "http://localhost:5177/" },
  payer: { name: "the partner app", url: "http://localhost:5177/" },
  employer: { name: "the partner app", url: "http://localhost:5177/" },
  developer: { name: "the partner app", url: "http://localhost:5177/" },
  patient: { name: "the patient app", url: "http://localhost:5174/" },
  operations: { name: "the staff console", url: "http://localhost:5173/" },
  clinician: { name: "the doctor app", url: "http://localhost:5176/" },
};

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [organization, setOrganization] = useState("");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [elsewhere, setElsewhere] = useState<string | null>(null);

  useEffect(() => {                       // development only: see shared/src/devLogin.ts
    const dev = readDevLogin();
    if (dev) void attempt(dev.org, dev.login, dev.pw);
  }, []);                                 // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await attempt(organization, login, password);
  }

  async function attempt(organization: string, login: string, password: string) {
    setBusy(true); setError(""); setElsewhere(null);
    try {
      const reply = await signIn(organization, login, password);
      if (reply.home !== "platform") {
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
    <AuthShell headline="Keep every customer's care" accent="running safely."
      subtitle="Customers, incidents, releases and safety work for Zoiko staff."
      points={["See the health of every customer at a glance", "Spot tampering or backlogs early", "Never see a patient's information"]}>
      <div className="auth-card">
        <h2>Platform sign in</h2>
        <p className="lead">For Zoiko support and safety staff only.</p>

        <SsoButtons />
        <div className="divider">or use your Zoiko login</div>

        <form onSubmit={(e) => void submit(e)}>
          <label htmlFor="org">Organization</label>
          <input id="org" className="field" value={organization} onChange={(e) => setOrganization(e.target.value)}
            autoComplete="organization" required />
          <label htmlFor="login">Login</label>
          <input id="login" className="field" value={login} onChange={(e) => setLogin(e.target.value)}
            autoComplete="username" required />
          <PasswordField id="password" label="Password" value={password} onChange={setPassword}
            autoComplete="current-password" />
          <RememberRow />
          {error && <div className="error" role="alert">{error}</div>}
          {elsewhere && (
            <div className="notice" role="alert">
              {other ? <>This account belongs in {other.name}. <a href={other.url}>Open it</a>.</>
                : <>This account does not have an app yet (role area: {elsewhere}).</>}
            </div>
          )}
          <button className="btn wide" type="submit" disabled={busy || !organization || !login || !password}>
            {busy ? "Signing in…" : "Log In"}
          </button>
        </form>
        <p className="switch">Every view in this console is recorded in the platform audit log.</p>
      </div>
    </AuthShell>
  );
}
