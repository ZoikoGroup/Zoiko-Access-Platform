import { finishSignIn } from "./signInStep";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./ui";

export interface AuthTab { id: string; label: string }
export type AuthPoint = string | { title: string; sub?: string; icon?: string };

/** The "ZOIKO ACCESS" wordmark, drawn in text so it needs no image file. */
export function Wordmark({ small = false }: { small?: boolean }) {
  return (
    <span className={`wordmark${small ? " small" : ""}`} role="img" aria-label="Zoiko Access">
      <b aria-hidden="true">ZOIKO</b><i aria-hidden="true">ACCESS</i>
    </span>
  );
}

/**
 * The sign-in / create-account frame: a brand panel on the left, the form on the right.
 * The headline is `headline` + `accent` (shown in the highlight colour) + `after`. `tabs`
 * (optional) switch between sign-in, register and reset, only where those flows exist.
 */
export function AuthShell({
  headline, accent, after, subtitle, points, chips, strip, children, tabs, tab, onTab,
}: {
  headline: string; accent?: string; after?: string; subtitle?: string; points: AuthPoint[];
  chips?: { title: string; sub: string }[]; strip?: string; children: ReactNode;
  tabs?: AuthTab[]; tab?: string; onTab?: (id: string) => void;
}) {
  return (
    <div className="auth">
      <aside className="auth-brand">
        <div className="brand"><Wordmark /></div>
        <h1>{headline}{accent && <> <span className="accent">{accent}</span></>}{after && <> {after}</>}</h1>
        {subtitle && <p className="auth-sub">{subtitle}</p>}
        <ul>
          {points.map((p) => {
            const point = typeof p === "string" ? { title: p } : p;
            return (
              <li key={point.title}>
                <span className="ring"><Icon name={point.icon ?? "check"} size={20} /></span>
                <span><b>{point.title}</b>{point.sub && <small>{point.sub}</small>}</span>
              </li>
            );
          })}
        </ul>
        {chips && (
          <div className="auth-chips" aria-hidden="true">
            {chips.map((c) => <div key={c.title} className="auth-chip"><b>{c.title}</b><small>{c.sub}</small></div>)}
          </div>
        )}
        <p className="fine">Your information stays in your region. Every access to it is recorded.</p>
        <div className="auth-strip" aria-hidden="true">{strip ?? "PATIENTS / CLINICS / DOCTORS / SAFETY"}</div>
      </aside>
      <main className="auth-form" id="main">
        <div className="auth-panel">
          {tabs && (
            <div className="auth-tabs" role="tablist" aria-label="Account">
              {tabs.map((t) => (
                <button key={t.id} type="button" role="tab" aria-selected={t.id === tab}
                  className={t.id === tab ? "on" : ""} onClick={() => onTab?.(t.id)}>{t.label}</button>
              ))}
            </div>
          )}
          <div className="auth-logo"><Wordmark /></div>
          {children}
          <div className="auth-foot">
            <span>Privacy</span><span aria-hidden="true">•</span><span>Terms</span><span aria-hidden="true">•</span>
            <span>Accessibility</span><span aria-hidden="true">•</span><span>Help</span>
            <span className="copy">© 2026 Zoiko Tech Inc.</span>
          </div>
        </div>
      </main>
    </div>
  );
}

export function PasswordField({ id, label, value, onChange, autoComplete }: {
  id: string; label: string; value: string; onChange: (v: string) => void; autoComplete: string;
}) {
  const [shown, setShown] = useState(false);
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <div className="pw">
        <input id={id} className="field" type={shown ? "text" : "password"} value={value}
          onChange={(e) => onChange(e.target.value)} autoComplete={autoComplete} required />
        <button type="button" className="pw-toggle" onClick={() => setShown(!shown)}
          aria-label={shown ? "Hide password" : "Show password"} aria-pressed={shown}>
          {shown ? "Hide" : "Show"}
        </button>
      </div>
    </>
  );
}

/**
 * "Remember me" and "Forgot password?". Remembering a login for 30 days is allowed for patients only (DEV-129).
 * Everyone else keeps the 8-hour login that is forgotten when the tab closes, so the box is shown
 * switched off for them.
 */
export function RememberRow({ onForgot, checked, onChange }: {
  onForgot?: () => void;
  checked?: boolean;                       // given only by the patient app, the only one that allows it
  onChange?: (value: boolean) => void;
}) {
  const allowed = onChange !== undefined;
  return (
    <div className="auth-row">
      <label className="remember" title={allowed ? "Stay signed in on this device for 30 days. Use only your own device. Signing out signs you out everywhere." : "A 30-day login is only for patients"}>
        <input type="checkbox" disabled={!allowed} checked={!!checked} onChange={(e) => onChange?.(e.target.checked)} /> Remember me for 30 days
      </label>
      {onForgot && <button type="button" className="linklike" onClick={onForgot}>Forgot password?</button>}
    </div>
  );
}

const GOOGLE_ICON = (
  <svg width="22" height="22" viewBox="0 0 48 48" aria-hidden="true">
    <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.6 5.9c4.4-4.1 7-10.1 7-17.6z" />
    <path fill="#FBBC05" d="M10.5 28.7a14.5 14.5 0 010-9.4l-7.9-6.1a24 24 0 000 21.6l7.9-6.1z" />
    <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.8 2.3-8.3 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z" />
  </svg>
);

interface SsoProvider { key: string; name: string }
const PENDING = "zoiko_sso_pending";

function readPending(): { provider: string } | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING);
    return raw ? (JSON.parse(raw) as { provider: string }) : null;
  } catch { return null; }
}
function savePending(value: { provider: string } | null) {
  try {
    if (value) window.sessionStorage.setItem(PENDING, JSON.stringify(value));
    else window.sessionStorage.removeItem(PENDING);
  } catch { /* private window: sign-in by provider still starts, it just cannot be finished here */ }
}

async function postJson(path: string, body: unknown): Promise<{ ok: boolean; data: unknown }> {
  const res = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const kind = res.headers.get("Content-Type") ?? "";
  return { ok: res.ok, data: kind.includes("json") ? await res.json() : null };
}

/** Sign in with an outside identity provider (Google, or the organization's own). Only providers the
 *  server really offers are shown. The person is sent to the provider and comes back to this page
 *  with a `code` and `state`, which this component finishes and hands to `onReply` as the same answer
 *  a password sign-in gives. Every failure says the same thing; the server does not say why. */
export function SsoButtons({ organization, onReply, onError }: {
  organization: string;
  onReply: (reply: unknown) => void | Promise<void>;
  onError: (message: string) => void;
}) {
  const [providers, setProviders] = useState<SsoProvider[]>([]);
  const [busy, setBusy] = useState(false);
  const finishing = useRef(false);
  const redirectUri = `${window.location.origin}/`;

  useEffect(() => {
    fetch("/api/v1/auth/providers", { cache: "no-store" })
      .then((r) => r.json())
      .then((b: { sso?: SsoProvider[] }) => setProviders(Array.isArray(b.sso) ? b.sso : []))
      .catch(() => setProviders([]));
  }, []);

  useEffect(() => {                      // coming back from the provider
    const here = new URL(window.location.href);
    const code = here.searchParams.get("code");
    const state = here.searchParams.get("state");
    const pending = readPending();
    if (!code || !state || !pending || finishing.current) return;
    finishing.current = true;
    savePending(null);
    window.history.replaceState({}, "", window.location.pathname);   // the code is used once: drop it
    setBusy(true);
    postJson("/api/v1/auth/sso/finish", { provider: pending.provider, code, state, redirect_uri: redirectUri })
      .then(async ({ ok, data }) => {
        if (!ok) throw new Error("failed");
        await onReply(await finishSignIn(data));
      })
      .catch(() => onError("Sign-in with your provider did not work. Try again, or use your login and password."))
      .finally(() => setBusy(false));
  }, []);                                // eslint-disable-line react-hooks/exhaustive-deps

  async function go(provider: SsoProvider) {
    if (!organization.trim()) { onError("Enter your organization first, then choose a provider."); return; }
    setBusy(true);
    try {
      const { ok, data } = await postJson("/api/v1/auth/sso/start", {
        provider: provider.key, organization, redirect_uri: redirectUri });
      const url = (data as { url?: string } | null)?.url;
      if (!ok || !url) throw new Error("failed");
      savePending({ provider: provider.key });
      window.location.assign(url);
    } catch {
      onError("Sign-in with that provider is not available. Use your login and password.");
      setBusy(false);
    }
  }

  if (providers.length === 0) return null;
  return (
    <div className="sso">
      {providers.map((p) => (
        <button key={p.key} type="button" className="sso-btn" disabled={busy} onClick={() => void go(p)}>
          <span className="sso-icon">{p.key === "google" ? GOOGLE_ICON : <Icon name="organizations" size={22} />}</span>
          <span className="sso-name">Continue with {p.name}</span>
          <span className="sso-go" aria-hidden="true">›</span>
        </button>
      ))}
    </div>
  );
}
