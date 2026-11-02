import { useState, type ReactNode } from "react";
import { Icon, Logo } from "./ui";

/** The sign-in / create-account frame: a brand panel on the left, the form on the right. */
export function AuthShell({ headline, points, children }: {
  headline: string; points: string[]; children: ReactNode;
}) {
  return (
    <div className="auth">
      <aside className="auth-brand">
        <div className="brand"><Logo size={44} /><div><b>Zoiko Access</b><small>Global Healthcare Access</small></div></div>
        <h1>{headline}</h1>
        <ul>
          {points.map((p) => <li key={p}><Icon name="check" size={18} />{p}</li>)}
        </ul>
        <p className="fine">Your information stays in your region. Every access to it is recorded.</p>
      </aside>
      <section className="auth-form">{children}</section>
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

export function GoogleButton() {
  return (
    <div>
      <button type="button" className="google" disabled
        title="Google sign-in needs a Google client id, which has not been set up yet">
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
          <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
          <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.6 5.9c4.4-4.1 7-10.1 7-17.6z" />
          <path fill="#FBBC05" d="M10.5 28.7a14.5 14.5 0 010-9.4l-7.9-6.1a24 24 0 000 21.6l7.9-6.1z" />
          <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.8 2.3-8.3 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z" />
        </svg>
        Continue with Google
      </button>
      <div className="hint">Google sign-in is not set up yet.</div>
    </div>
  );
}
