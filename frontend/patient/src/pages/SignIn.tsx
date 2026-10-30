import { useState } from "react";
import { AuthShell, GoogleButton, PasswordField } from "../AuthShell";
import { forgotPassword, registerAccount, setSession, signIn, type LoginReply, type Session } from "../api";

// Where each kind of person belongs. Only the staff console exists besides this app.
const OTHER_APPS: Record<string, { name: string; url: string }> = {
  operations: { name: "the staff console", url: "http://localhost:5173/" },
  platform: { name: "the platform console", url: "http://localhost:5175/" },
  clinician: { name: "the doctor app", url: "http://localhost:5176/" },
};

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [mode, setMode] = useState<"signin" | "register" | "forgot">("signin");
  const [organization, setOrganization] = useState("");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [elsewhere, setElsewhere] = useState<string | null>(null);

  const registering = mode === "register";

  function switchTo(next: "signin" | "register" | "forgot") {
    setMode(next); setError(""); setElsewhere(null); setPassword(""); setConfirm("");
  }

  if (mode === "forgot") {
    return <Forgot organization={organization} login={login} onBack={() => switchTo("signin")} />;
  }

  function finish(reply: LoginReply) {
    if (reply.home !== "patient") {
      setElsewhere(reply.home);              // right password, wrong app: do not keep the token
      setPassword("");
      return;
    }
    const session = { token: reply.token, tenantName: reply.tenant.name, login: reply.actor.login };
    setSession(session);
    onSignedIn(session);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setElsewhere(null);
    if (registering && password !== confirm) { setError("The two passwords do not match."); return; }
    setBusy(true);
    try {
      finish(registering
        ? await registerAccount(organization, login, password)
        : await signIn(organization, login, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setPassword(""); setConfirm("");
    } finally { setBusy(false); }
  }

  const other = elsewhere ? OTHER_APPS[elsewhere] : undefined;
  return (
    <AuthShell headline="Your care, all in one place."
      points={["Find care and book appointments", "Keep your family's care together", "See who has looked at your information"]}>
      <div className="auth-card">
        <h2>{registering ? "Create your account" : "Welcome back"}</h2>
        <p className="lead">{registering ? "It takes a minute." : "Sign in to your Zoiko Access account."}</p>

        <GoogleButton />
        <div className="divider">or use your email</div>

        <form onSubmit={(e) => void submit(e)}>
          <label htmlFor="org">Organization</label>
          <input id="org" className="field" value={organization} onChange={(e) => setOrganization(e.target.value)}
            autoComplete="organization" placeholder="The clinic group you are registered with" required />
          <label htmlFor="login">Email</label>
          <input id="login" className="field" type={registering ? "email" : "text"} value={login}
            onChange={(e) => setLogin(e.target.value)} autoComplete={registering ? "email" : "username"} required />
          <PasswordField id="password" label="Password" value={password} onChange={setPassword}
            autoComplete={registering ? "new-password" : "current-password"} />
          {!registering && (
            <button type="button" className="linklike" onClick={() => switchTo("forgot")}>Forgot password?</button>
          )}
          {registering && (
            <PasswordField id="confirm" label="Confirm password" value={confirm} onChange={setConfirm}
              autoComplete="new-password" />
          )}
          {registering && <p className="fine-print">Use a long password that is not your email. Your email is not verified yet.</p>}
          {error && <div className="error" role="alert">{error}</div>}
          {elsewhere && (
            <div className="notice" role="alert">
              {other ? <>This account belongs in {other.name}. <a href={other.url}>Open it</a>.</>
                : <>This account does not have an app yet (role area: {elsewhere}).</>}
            </div>
          )}
          <button className="btn wide" type="submit"
            disabled={busy || !organization || !login || !password || (registering && !confirm)}>
            {busy ? "Please wait…" : registering ? "Create account" : "Sign in"}
          </button>
        </form>

        <p className="switch">
          {registering ? <>Already have an account? <button onClick={() => switchTo("signin")}>Sign in</button></>
            : <>New to Zoiko Access? <button onClick={() => switchTo("register")}>Create an account</button></>}
        </p>
      </div>
    </AuthShell>
  );
}

/** "Forgot password": always shows the same confirmation, so it never reveals who has an account. */
function Forgot({ organization, login, onBack }: { organization: string; login: string; onBack: () => void }) {
  const [org, setOrg] = useState(organization);
  const [email, setEmail] = useState(login);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await forgotPassword(org, email);
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally { setBusy(false); }
  }

  return (
    <AuthShell headline="Your care, all in one place."
      points={["Find care and book appointments", "Keep your family's care together", "See who has looked at your information"]}>
      <div className="auth-card">
        <h2>Reset your password</h2>
        {sent ? (
          <>
            <p className="lead">
              If that account exists and its email address has been verified, we have sent a link to reset
              the password. It works once and expires in an hour.
            </p>
            <button className="btn wide" onClick={onBack}>Back to sign in</button>
          </>
        ) : (
          <form onSubmit={(e) => void submit(e)}>
            <p className="lead">Enter your organization and email. We will email you a link.</p>
            <label htmlFor="f-org">Organization</label>
            <input id="f-org" className="field" value={org} onChange={(e) => setOrg(e.target.value)} required />
            <label htmlFor="f-email">Email</label>
            <input id="f-email" className="field" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            {error && <div className="error" role="alert">{error}</div>}
            <button className="btn wide" type="submit" disabled={busy || !org || !email}>{busy ? "Please wait…" : "Send reset link"}</button>
            <p className="switch"><button type="button" onClick={onBack}>Back to sign in</button></p>
          </form>
        )}
      </div>
    </AuthShell>
  );
}
