import { useState, type ReactNode } from "react";
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
 * "Remember me" and "Forgot password?". Remembering a login for 30 days is a security decision that
 * has not been made (a login lasts 8 hours and is forgotten when the tab closes), so the box is
 * shown switched off and says so.
 */
export function RememberRow({ onForgot }: { onForgot?: () => void }) {
  return (
    <div className="auth-row">
      <label className="remember" title="A 30-day login has not been set up yet">
        <input type="checkbox" disabled /> Remember me for 30 days
      </label>
      {onForgot && <button type="button" className="linklike" onClick={onForgot}>Forgot password?</button>}
    </div>
  );
}

const SSO: { name: string; icon: ReactNode }[] = [
  { name: "Microsoft", icon: (
    <svg width="22" height="22" viewBox="0 0 21 21" aria-hidden="true">
      <rect width="10" height="10" fill="#F25022" /><rect x="11" width="10" height="10" fill="#7FBA00" />
      <rect y="11" width="10" height="10" fill="#00A4EF" /><rect x="11" y="11" width="10" height="10" fill="#FFB900" />
    </svg>) },
  { name: "Google", icon: (
    <svg width="22" height="22" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.6 5.9c4.4-4.1 7-10.1 7-17.6z" />
      <path fill="#FBBC05" d="M10.5 28.7a14.5 14.5 0 010-9.4l-7.9-6.1a24 24 0 000 21.6l7.9-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.8 2.3-8.3 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z" />
    </svg>) },
  { name: "Apple", icon: (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">
      <circle cx="9" cy="14" r="5.5" /><circle cx="15" cy="14" r="5.5" />
      <path d="M12 8c0-2.2.9-3.8 3-4.8.2 2-.8 3.8-3 4.8z" />
    </svg>) },
  { name: "Company SSO", icon: <Icon name="organizations" size={22} /> },
];

/** Outside sign-in options. None is connected yet, so they are shown switched off, with the reason. */
export function SsoButtons() {
  return (
    <div className="sso">
      {SSO.map((s) => (
        <button key={s.name} type="button" className="sso-btn" disabled
          title="Single sign-in has not been set up yet">
          <span className="sso-icon">{s.icon}</span><span className="sso-name">Continue with {s.name}</span>
          <span className="sso-go" aria-hidden="true">›</span>
        </button>
      ))}
      <div className="hint">Single sign-in is not set up yet. Use your email and password.</div>
    </div>
  );
}

/** Kept for the older name: the same switched-off options. */
export const GoogleButton = SsoButtons;
