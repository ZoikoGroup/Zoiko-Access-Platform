import { useEffect, useState } from "react";
import { readDevLogin } from "@shared/devLogin";
import { t } from "@shared/i18n";
import { SecondStepForm } from "@shared/TwoStep";
import { AuthShell, PasswordField, RememberRow, SsoButtons } from "../AuthShell";
import { finishSecondStep, forgotPassword, registerAccount, setSession, signIn, type LoginReply, type Session } from "../api";

// Where each kind of person belongs. Only the staff console exists besides this app.
const OTHER_APPS: Record<string, { name: string; url: string }> = {
  partner: { name: "the partner app", url: "http://localhost:5177/" },
  payer: { name: "the partner app", url: "http://localhost:5177/" },
  employer: { name: "the partner app", url: "http://localhost:5177/" },
  developer: { name: "the partner app", url: "http://localhost:5177/" },
  operations: { name: "the staff console", url: "http://localhost:5173/" },
  platform: { name: "the platform console", url: "http://localhost:5175/" },
  clinician: { name: "the doctor app", url: "http://localhost:5176/" },
};

// What the left panel says. Two of the owner's lines were softened because nothing backs them yet
// ("trusted and compliant": no compliance review; "better outcomes": nothing measures outcomes).
const BRAND = {
  headline: "Your gateway to a", accent: "healthier, smarter", after: "tomorrow.",
  subtitle: "Access care, services and insights in one secure platform, for individuals, families and organisations.",
  points: [
    { title: "Connected healthcare", sub: "People • Providers • Services", icon: "patients" },
    { title: "Private by design", sub: "Privacy • Security • Governance", icon: "shield" },
    { title: "Simpler access", sub: "Find, book and manage with ease", icon: "services" },
    { title: "Clear insights", sub: "See your care and who has looked at it", icon: "reports" },
  ],
  chips: [
    { title: "Care", sub: "Made easier" }, { title: "Appointments", sub: "Simplified" },
    { title: "Health", sub: "On your terms" },
  ],
  strip: "PEOPLE / CARE / SERVICES / INSIGHTS / A HEALTHIER TOMORROW",
};

const TABS = [
  { id: "signin", label: "Login" }, { id: "register", label: "Register" }, { id: "forgot", label: "Reset password" },
];

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [mode, setMode] = useState<"signin" | "register" | "forgot">("signin");
  const [organization, setOrganization] = useState("");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [elsewhere, setElsewhere] = useState<string | null>(null);
  const [remember, setRemember] = useState(false);
  const [ticket, setTicket] = useState("");
  const [assist, setAssist] = useState(false);

  const registering = mode === "register";

  useEffect(() => {                       // development only: see shared/src/devLogin.ts
    const dev = readDevLogin();
    if (dev) void devSignIn(dev.org, dev.login, dev.pw);
  }, []);                                 // eslint-disable-line react-hooks/exhaustive-deps

  function switchTo(next: "signin" | "register" | "forgot") {
    setMode(next); setError(""); setElsewhere(null); setPassword(""); setConfirm("");
  }

  if (mode === "forgot") {
    return <Forgot organization={organization} login={login} onBack={() => switchTo("signin")}
      onTab={(id) => switchTo(id)} />;
  }

  function finish(reply: LoginReply) {
    if (reply.home !== "patient") {
      setElsewhere(reply.home);              // right password, wrong app: do not keep the token
      setPassword("");
      return;
    }
    const session: Session = { token: reply.token, tenantName: reply.tenant.name, login: reply.actor.login };
    if (reply.remembered && reply.expires_at) session.expiresAt = reply.expires_at;
    setSession(session);
    onSignedIn(session);
  }

  async function devSignIn(org: string, who: string, secret: string) {   // development only
    setError(""); setElsewhere(null); setBusy(true);
    try {
      const reply = await signIn(org, who, secret);
      if (reply.second_step && reply.ticket) { setTicket(reply.ticket); return; }
      finish(reply);
    }
    catch (err) { setError(err instanceof Error ? err.message : "Something went wrong"); }
    finally { setBusy(false); }
  }

  async function enterCode(code: string) {
    setBusy(true); setError("");
    try {
      const reply = await finishSecondStep(ticket, code);
      setTicket("");
      finish(reply);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally { setBusy(false); }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setElsewhere(null);
    if (registering && password !== confirm) { setError("The two passwords do not match."); return; }
    setBusy(true);
    try {
      const reply = registering
        ? await registerAccount(organization, login, password)
        : await signIn(organization, login, password, remember);
      if (reply.second_step && reply.ticket) {
        setTicket(reply.ticket);
        setPassword("");
        return;
      }
      finish(reply);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setPassword(""); setConfirm("");
    } finally { setBusy(false); }
  }

  if (ticket) {
    return (
      <AuthShell {...BRAND}
        tabs={TABS} tab={mode} onTab={(id) => { setTicket(""); switchTo(id as "signin" | "register" | "forgot"); }}>
        <div className="auth-card">
          <h2>{t("auth.twoStepTitle")}</h2>
          <p className="lead">{t("auth.twoStepLead")}</p>
          <SecondStepForm busy={busy} error={error} onCode={(c: string) => void enterCode(c)}
            onBack={() => { setTicket(""); setError(""); }} />
        </div>
      </AuthShell>
    );
  }

  const other = elsewhere ? OTHER_APPS[elsewhere] : undefined;
  return (
    <AuthShell {...BRAND}
      tabs={TABS} tab={mode} onTab={(id) => switchTo(id as "signin" | "register" | "forgot")}>
      <div className="auth-card">
        <h2>{registering ? "Create your account" : "Welcome back!"}</h2>
        <p className="lead">{registering ? "It takes a minute." : "Sign in to your Zoiko Access account."}</p>

        <SsoButtons />
        <div className="divider">or use your email</div>

        <form onSubmit={(e) => void submit(e)}>
          <label htmlFor="org">{t("common.organization")}</label>
          <input id="org" className="field" value={organization} onChange={(e) => setOrganization(e.target.value)}
            autoComplete="organization" placeholder={t("auth.organizationHint")} required />
          <label htmlFor="login">{t("common.email")}</label>
          <input id="login" className="field" type={registering ? "email" : "text"} value={login}
            onChange={(e) => setLogin(e.target.value)} autoComplete={registering ? "email" : "username"} required />
          <PasswordField id="password" label={t("common.password")} value={password} onChange={setPassword}
            autoComplete={registering ? "new-password" : "current-password"} />
          {!registering && <RememberRow onForgot={() => switchTo("forgot")} checked={remember} onChange={setRemember} />}
          {!registering && remember && <p className="fine-print">{t("auth.rememberNote")}</p>}
          {registering && (
            <PasswordField id="confirm" label={t("common.confirmPassword")} value={confirm} onChange={setConfirm}
              autoComplete="new-password" />
          )}
          {registering && <p className="fine-print">{t("auth.passwordNote")}</p>}
          {error && <div className="error" role="alert">{error}</div>}
          {elsewhere && (
            <div className="notice" role="alert">
              {other ? <>{t("auth.otherAccount", { name: other.name })} <a href={other.url}>{t("auth.openIt")}</a>.</>
                : <>This account does not have an app yet (role area: {elsewhere}).</>}
            </div>
          )}
          <button className="btn wide" type="submit"
            disabled={busy || !organization || !login || !password || (registering && !confirm)}>
            {busy ? "Please wait…" : registering ? "Create account" : "Log In"}
          </button>
        </form>

        <p className="switch">
          {registering ? <>{t("auth.haveAccount")} <button onClick={() => switchTo("signin")}>{t("common.signIn")}</button></>
            : <>{t("auth.newHere")} <button onClick={() => switchTo("register")}>{t("common.register")}</button></>}
          {" · "}
          <button type="button" onClick={() => setAssist((v) => !v)}>
            {assist ? "Hide help" : "Get assistance"}
          </button>
        </p>
        {assist && (
          <div className="notice" role="region" aria-label="Sign-in assistance">
            <b>{t("auth.needHelp")}</b>
            <p style={{ margin: "6px 0" }}>
              Use the exact clinic group name on your invitation or appointment email. If you forgot your
              password, <button type="button" className="link" onClick={() => switchTo("forgot")}>reset it here</button>.
              If your clinic gave you an invitation link, open that link to set your first password.
            </p>
            <small>
              Zoiko Access is not an emergency service. If you need urgent medical help, contact your local
              emergency number straight away.
            </small>
          </div>
        )}
      </div>
    </AuthShell>
  );
}

/** "Forgot password": always shows the same confirmation, so it never reveals who has an account. */
function Forgot({ organization, login, onBack, onTab }: {
  organization: string; login: string; onBack: () => void; onTab: (id: "signin" | "register") => void;
}) {
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
    <AuthShell {...BRAND}
      tabs={TABS} tab="forgot" onTab={(id) => { if (id !== "forgot") onTab(id as "signin" | "register"); }}>
      <div className="auth-card">
        <h2>{t("auth.resetTitle")}</h2>
        {sent ? (
          <>
            <p className="lead">
              If that account exists and its email address has been verified, we have sent a link to reset
              the password. It works once and expires in an hour.
            </p>
            <button className="btn wide" onClick={onBack}>{t("common.backToSignIn")}</button>
          </>
        ) : (
          <form onSubmit={(e) => void submit(e)}>
            <p className="lead">{t("auth.resetLead")}</p>
            <label htmlFor="f-org">{t("common.organization")}</label>
            <input id="f-org" className="field" value={org} onChange={(e) => setOrg(e.target.value)} required />
            <label htmlFor="f-email">{t("common.email")}</label>
            <input id="f-email" className="field" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            {error && <div className="error" role="alert">{error}</div>}
            <button className="btn wide" type="submit" disabled={busy || !org || !email}>{busy ? "Please wait…" : "Send reset link"}</button>
            <p className="switch"><button type="button" onClick={onBack}>{t("common.backToSignIn")}</button></p>
          </form>
        )}
      </div>
    </AuthShell>
  );
}
