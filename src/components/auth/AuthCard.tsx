"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Building2, ChevronRight, Eye, EyeOff, Lock, Mail, UserRound, CircleCheck } from "lucide-react";
import Logo from "@/components/ui/Logo";
import Spinner from "@/components/ui/Spinner";
import BrandLoader from "@/components/ui/BrandLoader";

type Tab = "login" | "register" | "reset";
const tabs: { id: Tab; label: string }[] = [
  { id: "login", label: "Login" },
  { id: "register", label: "Register" },
  { id: "reset", label: "Reset Password" },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function AuthCard() {
  const [tab, setTab] = useState<Tab>("login");
  const [redirecting, setRedirecting] = useState(false);
  const router = useRouter();
  const activeIndex = tabs.findIndex((t) => t.id === tab);

  // TODO: replace with real auth (NextAuth / your API) — this only simulates a sign-in.
  async function enterDashboard() {
    setRedirecting(true);
    await wait(900);
    router.push("/dashboard");
  }

  return (
    <>
      {redirecting && <BrandLoader label="Signing in" />}
      <div className="w-full max-w-[460px] overflow-hidden rounded-2xl border border-slate-200/70 bg-card shadow-[0_20px_60px_-25px_rgba(36,59,115,0.35)] animate-fade-up">
        <div className="relative grid grid-cols-3 border-b border-slate-200">
          {tabs.map((t) => (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
              className={`py-4 text-[13px] font-medium transition-colors sm:text-sm ${tab === t.id ? "text-navy-900" : "text-slate-500 hover:text-brand"}`}>
              {t.label}
            </button>
          ))}
          <span className="absolute bottom-0 h-0.5 w-1/3 rounded-full bg-brand transition-transform duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={{ transform: `translateX(${activeIndex * 100}%)` }} />
        </div>

        <div key={tab} className="px-5 pb-6 pt-7 animate-fade-up sm:px-10">
          <Logo className="mx-auto h-14 w-auto" priority />
          {tab === "login" && <LoginForm onSuccess={enterDashboard} onRegister={() => setTab("register")} onForgot={() => setTab("reset")} />}
          {tab === "register" && <RegisterForm onSuccess={enterDashboard} onLogin={() => setTab("login")} />}
          {tab === "reset" && <ResetForm onLogin={() => setTab("login")} />}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-5 py-4 text-xs text-slate-500 sm:px-10">
          <div className="flex items-center gap-2">
            {["Privacy", "Terms", "Accessibility", "Help"].map((l, i) => (
              <span key={l} className="flex items-center gap-2">
                {i > 0 && <span className="text-slate-300">•</span>}
                <a href="#" className="transition hover:text-brand">{l}</a>
              </span>
            ))}
          </div>
          <span>© 2026 Zoiko Tech Inc.</span>
        </footer>
      </div>
    </>
  );
}

/* ---------------- Forms ---------------- */

function Heading({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mt-2 text-center">
      <h2 className="text-[1.75rem] font-bold sm:text-[2rem] tracking-tight text-navy-900">{title}</h2>
      <p className="mt-1 text-sm text-slate-500">{sub}</p>
    </div>
  );
}

function LoginForm({ onSuccess, onRegister, onForgot }: { onSuccess: () => void; onRegister: () => void; onForgot: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState<string | null>(null);
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next: typeof errors = {};
    if (!EMAIL_RE.test(email)) next.email = "Enter a valid work email address.";
    if (password.length < 6) next.password = "Password must be at least 6 characters.";
    setErrors(next);
    if (Object.keys(next).length) return;
    setLoading("email");
    await wait(1100);
    onSuccess();
  }

  async function sso(provider: string) {
    setLoading(provider);
    await wait(1100);
    onSuccess();
  }

  const providers = [
    { id: "microsoft", label: "Continue with Microsoft", icon: <MicrosoftIcon /> },
    { id: "google", label: "Continue with Google", icon: <GoogleIcon /> },
    { id: "apple", label: "Continue with Apple", icon: <AppleIcon /> },
    { id: "sso", label: "Continue with Company SSO", icon: <Building2 className="size-5 text-navy-900" /> },
  ];

  return (
    <>
      <Heading title="Welcome back!" sub="Sign in to your Zoiko Access account." />
      <div className="mt-6 space-y-2.5">
        {providers.map((p) => (
          <button key={p.id} type="button" disabled={!!loading} onClick={() => sso(p.id)}
            className="group flex w-full items-center gap-3 rounded-lg border border-slate-200 bg-card px-4 py-2.5 text-sm font-medium text-navy-900 transition duration-300 hover:-translate-y-0.5 hover:border-brand/40 hover:bg-brand-50/60 hover:shadow-[0_8px_20px_-10px_rgba(106,60,240,0.5)] disabled:opacity-60">
            <span className="grid size-6 place-items-center">{loading === p.id ? <Spinner className="size-4 text-brand" /> : p.icon}</span>
            <span className="flex-1 text-left">{p.label}</span>
            <ChevronRight className="size-4 text-slate-400 transition group-hover:translate-x-1 group-hover:text-brand" />
          </button>
        ))}
      </div>

      <Divider>or sign in with email</Divider>

      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Work email address" icon={<Mail className="size-4" />} error={errors.email}>
          <input type="email" autoComplete="email" placeholder="name@company.com" value={email}
            onChange={(e) => setEmail(e.target.value)} className={inputCls} />
        </Field>
        <PasswordField value={password} onChange={setPassword} error={errors.password} />

        <div className="flex flex-wrap items-center justify-between gap-2 text-[13px] sm:text-sm">
          <label className="flex cursor-pointer items-center gap-2 whitespace-nowrap text-slate-700">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)}
              className="size-4 cursor-pointer rounded accent-brand" />
            Remember me for 30 days
          </label>
          <button type="button" onClick={onForgot} className="font-medium text-brand transition hover:text-brand-700 hover:underline">
            Forgot password?
          </button>
        </div>

        <PrimaryButton loading={loading === "email"} disabled={!!loading}>Log In</PrimaryButton>
      </form>

      <p className="mt-5 text-center text-sm text-slate-600">
        New to Zoiko Access?{" "}
        <button type="button" onClick={onRegister} className="font-semibold text-brand hover:underline">Register</button>
      </p>
    </>
  );
}

function RegisterForm({ onSuccess, onLogin }: { onSuccess: () => void; onLogin: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", org: "", password: "" });
  const [agree, setAgree] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!form.name.trim()) next.name = "Enter your full name.";
    if (!EMAIL_RE.test(form.email)) next.email = "Enter a valid work email address.";
    if (form.password.length < 8) next.password = "Use at least 8 characters.";
    if (!agree) next.agree = "Please accept the terms to continue.";
    setErrors(next);
    if (Object.keys(next).length) return;
    setLoading(true);
    await wait(1200);
    onSuccess();
  }

  return (
    <>
      <Heading title="Create account" sub="Join Zoiko Access in under a minute." />
      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <Field label="Full name" icon={<UserRound className="size-4" />} error={errors.name}>
          <input placeholder="Jane Cooper" value={form.name} onChange={(e) => set("name")(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Work email address" icon={<Mail className="size-4" />} error={errors.email}>
          <input type="email" placeholder="name@company.com" value={form.email} onChange={(e) => set("email")(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Organisation (optional)" icon={<Building2 className="size-4" />}>
          <input placeholder="Company or practice name" value={form.org} onChange={(e) => set("org")(e.target.value)} className={inputCls} />
        </Field>
        <PasswordField value={form.password} onChange={set("password")} error={errors.password} placeholder="Create a password" />
        <div>
          <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-0.5 size-4 rounded accent-brand" />
            <span>I agree to the <a href="#" className="font-medium text-brand hover:underline">Terms</a> and <a href="#" className="font-medium text-brand hover:underline">Privacy Policy</a></span>
          </label>
          {errors.agree && <p className="mt-1 text-xs text-rose-500">{errors.agree}</p>}
        </div>
        <PrimaryButton loading={loading} disabled={loading}>Create Account</PrimaryButton>
      </form>
      <p className="mt-5 text-center text-sm text-slate-600">
        Already have an account?{" "}
        <button type="button" onClick={onLogin} className="font-semibold text-brand hover:underline">Log in</button>
      </p>
    </>
  );
}

function ResetForm({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string>();
  const [state, setState] = useState<"idle" | "loading" | "sent">("idle");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!EMAIL_RE.test(email)) return setError("Enter a valid work email address.");
    setError(undefined);
    setState("loading");
    await wait(1200);
    setState("sent");
  }

  if (state === "sent") {
    return (
      <div className="py-6 text-center animate-fade-up">
        <span className="mx-auto grid size-16 place-items-center rounded-full bg-emerald-50 text-emerald-500">
          <CircleCheck className="size-8" />
        </span>
        <h2 className="mt-4 text-2xl font-bold text-navy-900">Check your inbox</h2>
        <p className="mt-2 text-sm text-slate-500">We sent a reset link to <b className="text-navy-900">{email}</b>.</p>
        <button type="button" onClick={onLogin} className="mt-6 text-sm font-semibold text-brand hover:underline">Back to login</button>
      </div>
    );
  }

  return (
    <>
      <Heading title="Reset password" sub="We'll email you a secure link to reset it." />
      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <Field label="Work email address" icon={<Mail className="size-4" />} error={error}>
          <input type="email" placeholder="name@company.com" value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} />
        </Field>
        <PrimaryButton loading={state === "loading"} disabled={state === "loading"}>Send Reset Link</PrimaryButton>
      </form>
      <p className="mt-5 text-center text-sm text-slate-600">
        Remembered it?{" "}
        <button type="button" onClick={onLogin} className="font-semibold text-brand hover:underline">Log in</button>
      </p>
    </>
  );
}

/* ---------------- Primitives ---------------- */

const inputCls =
  "w-full rounded-lg border border-slate-200 bg-card py-2.5 pl-10 pr-10 text-sm text-navy-900 outline-none transition placeholder:text-slate-400 hover:border-brand/40 focus:border-brand focus:ring-4 focus:ring-brand/15";

function Field({ label, icon, error, children }: { label: string; icon: ReactNode; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-navy-900">{label}</span>
      <span className="group relative block">
        <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 transition group-focus-within:text-brand">{icon}</span>
        {children}
      </span>
      {error && <span className="mt-1 block text-xs text-rose-500 animate-fade-up">{error}</span>}
    </label>
  );
}

function PasswordField({ value, onChange, error, placeholder = "Enter your password" }: { value: string; onChange: (v: string) => void; error?: string; placeholder?: string }) {
  const [show, setShow] = useState(false);
  return (
    <Field label="Password" icon={<Lock className="size-4" />} error={error}>
      <input type={show ? "text" : "password"} autoComplete="current-password" placeholder={placeholder}
        value={value} onChange={(e) => onChange(e.target.value)} className={inputCls} />
      <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 transition hover:text-brand">
        {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </Field>
  );
}

function PrimaryButton({ children, loading, disabled }: { children: ReactNode; loading?: boolean; disabled?: boolean }) {
  return (
    <button type="submit" disabled={disabled}
      className="shine flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-brand to-[#7d4cf5] py-3 text-sm font-semibold text-white shadow-glow transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-10px_rgba(106,60,240,0.7)] active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-80">
      {loading && <Spinner className="size-4" />}
      {loading ? "Please wait…" : children}
    </button>
  );
}

function Divider({ children }: { children: ReactNode }) {
  return (
    <div className="my-5 flex items-center gap-3 text-xs text-slate-400">
      <span className="h-px flex-1 bg-slate-200" />{children}<span className="h-px flex-1 bg-slate-200" />
    </div>
  );
}

function MicrosoftIcon() {
  return (
    <svg viewBox="0 0 21 21" className="size-[18px]" aria-hidden>
      <path fill="#F25022" d="M0 0h10v10H0z" /><path fill="#7FBA00" d="M11 0h10v10H11z" />
      <path fill="#00A4EF" d="M0 11h10v10H0z" /><path fill="#FFB900" d="M11 11h10v10H11z" />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" className="size-5" aria-hidden>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2c-2 1.5-4.5 2.4-7.2 2.4-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}

function AppleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-5 text-navy-900" fill="currentColor" aria-hidden>
      <path d="M16.37 12.6c-.02-2.3 1.88-3.4 1.96-3.46-1.07-1.56-2.73-1.78-3.32-1.8-1.41-.14-2.76.83-3.48.83-.72 0-1.82-.81-3-.79-1.54.02-2.96.9-3.76 2.27-1.6 2.78-.41 6.9 1.15 9.16.76 1.1 1.67 2.34 2.86 2.3 1.15-.05 1.58-.74 2.97-.74 1.38 0 1.77.74 2.98.72 1.23-.02 2.01-1.12 2.76-2.23.87-1.28 1.23-2.52 1.25-2.58-.03-.01-2.4-.92-2.42-3.65zM14.1 5.86c.63-.77 1.06-1.83.94-2.89-.91.04-2.01.61-2.66 1.37-.58.67-1.09 1.75-.95 2.79 1.01.08 2.04-.51 2.67-1.27z" />
    </svg>
  );
}
