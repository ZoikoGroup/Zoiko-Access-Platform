import { useEffect, useState } from "react";
import { readDevLogin } from "@shared/devLogin";
import { RequiredSetUp, SecondStepForm } from "@shared/TwoStep";
import { AuthShell, PasswordField, RememberRow, SsoButtons } from "../AuthShell";
import { finishSecondStep, getSession, setSession, signIn, signOutEverywhere, twoStepApi, type LoginReply, type Session } from "../api";

// Where each kind of person belongs, when it is not here.
const OTHER_APPS: Record<string, { name: string; url: string }> = {
  patient: { name: "the patient app", url: "http://localhost:5174/" },
  operations: { name: "the staff console", url: "http://localhost:5173/" },
  clinician: { name: "the doctor app", url: "http://localhost:5176/" },
  platform: { name: "the platform console", url: "http://localhost:5175/" },
};
const HERE = new Set(["partner", "payer", "employer", "developer"]);

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [organization, setOrganization] = useState("");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [elsewhere, setElsewhere] = useState<string | null>(null);
  // Two-step sign-in (DEV-380): the ticket while the code is due, and a required set-up.
  const [ticket, setTicket] = useState("");
  const [settingUp, setSettingUp] = useState(false);

  useEffect(() => {                       // development only: see shared/src/devLogin.ts
    const dev = readDevLogin();
    if (dev) void attempt(dev.org, dev.login, dev.pw);
  }, []);                                 // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await attempt(organization, login, password);
  }

  /** A session from sign-in (with or without the code): only for this app's people. */
  function arrive(reply: LoginReply) {
    if (!HERE.has(reply.home)) {
      setElsewhere(reply.home);            // right password, wrong app: do not keep the token
      setPassword("");
      return;
    }
    const session: Session = {
      token: reply.token, tenantName: reply.tenant.name, login: reply.actor.login, area: reply.home,
      roles: reply.roles,
    };
    setSession(session);
    if (reply.must_set_up_two_step) setSettingUp(true);   // the organization requires it
    else onSignedIn(session);
  }

  async function enterCode(code: string) {
    setBusy(true); setError("");
    try {
      const reply = await finishSecondStep(ticket, code);
      setTicket("");
      arrive(reply);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally { setBusy(false); }
  }

  async function attempt(organization: string, login: string, password: string) {
    setBusy(true); setError(""); setElsewhere(null);
    try {
      const reply = await signIn(organization, login, password);
      if (reply.second_step && reply.ticket) {      // right password: now the code
        setTicket(reply.ticket);
        setPassword("");
        return;
      }
      arrive(reply);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setPassword("");
    } finally { setBusy(false); }
  }

  if (settingUp) {
    return (
      <AuthShell headline="Partners, payers and employers," accent="one front door."
        subtitle="For laboratories, pharmacies, home-care partners, insurers and employer sponsors working with Zoiko Access."
        points={["See exactly what your role may reach", "Nothing is shown that your role is not allowed to see", "Everything you do is recorded"]}>
        <RequiredSetUp api={twoStepApi}
          onDone={() => { const now = getSession(); setSettingUp(false); if (now) onSignedIn(now); }}
          onCancel={() => { void signOutEverywhere(); setSettingUp(false); }} />
      </AuthShell>
    );
  }
  if (ticket) {
    return (
      <AuthShell headline="Partners, payers and employers," accent="one front door."
        subtitle="For laboratories, pharmacies, home-care partners, insurers and employer sponsors working with Zoiko Access."
        points={["See exactly what your role may reach", "Nothing is shown that your role is not allowed to see", "Everything you do is recorded"]}>
        <div className="auth-card">
          <h2>Two-step sign-in</h2>
          <p className="lead">Your password was right. Now type the 6-digit code your authenticator app shows.</p>
          <SecondStepForm busy={busy} error={error} onCode={(c: string) => void enterCode(c)}
            onBack={() => { setTicket(""); setError(""); }} />
        </div>
      </AuthShell>
    );
  }

  const other = elsewhere ? OTHER_APPS[elsewhere] : undefined;
  return (
    <AuthShell headline="Partners, payers and employers," accent="one front door."
      subtitle="For laboratories, pharmacies, home-care partners, insurers and employer sponsors working with Zoiko Access."
      points={["See exactly what your role may reach", "Nothing is shown that your role is not allowed to see", "Everything you do is recorded"]}>
      <div className="auth-card">
        <h2>Partner sign in</h2>
        <p className="lead">For partner, payer and employer accounts.</p>

        <SsoButtons />
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
        <p className="switch">New here? Zoiko support sends you an invitation email.</p>
      </div>
    </AuthShell>
  );
}
