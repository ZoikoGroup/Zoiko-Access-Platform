import { t } from "./i18n";
import { useEffect, useState } from "react";
import { Card } from "./ui";

export interface EmailStatus {
  email: string;          // masked by the server, e.g. a***@example.org; empty if none
  verified: boolean;
}

/** "My email address": the masked address and whether it is verified, a way to set or change it
 *  (the current password is required, and the new address starts unverified), and a button to
 *  send the verification link again. The app supplies the three server calls; errors are thrown
 *  as Errors with a plain message. */
export function EmailCard({ load, onSave, onResend }: {
  load: () => Promise<EmailStatus>;
  onSave: (email: string, password: string) => Promise<EmailStatus & { sent: boolean }>;
  onResend: () => Promise<"sent" | "wait">;
}) {
  const [status, setStatus] = useState<EmailStatus | null>(null);
  const [editing, setEditing] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    load().then(setStatus).catch((e) => setError(e instanceof Error ? e.message : t("common.couldNotLoad")));
  }, [load]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) { setError(t("email.enterBoth")); return; }
    setBusy(true); setError(""); setNote("");
    try {
      const done = await onSave(email.trim(), password);
      setStatus({ email: done.email, verified: done.verified });
      setNote(done.sent
        ? t("email.linkSent")
        : t("email.savedWait"));
      setEditing(false); setEmail(""); setPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("email.couldNotSave"));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true); setError(""); setNote("");
    try {
      setNote((await onResend()) === "sent" ? t("email.sentAgain") : t("email.waitMinute"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("email.couldNotSend"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("email.title")}
      sub={t("email.sub")}>
      {status === null ? <div className="empty">{error || "Loading…"}</div> : (
        <>
          <p>
            {status.email
              ? <><b>{status.email}</b> {status.verified ? "(confirmed)" : "(not confirmed yet)"}</>
              : t("email.none")}
          </p>
          {status.email && !status.verified && !editing && (
            <button className="btn ghost" disabled={busy} onClick={() => void resend()}>Send the confirmation link again</button>
          )}{" "}
          {!editing && <button className="btn ghost" onClick={() => { setEditing(true); setNote(""); }}>{status.email ? "Change address" : "Add an address"}</button>}
          {editing && (
            <form onSubmit={(e) => void save(e)} style={{ display: "grid", gap: 8, maxWidth: 360, marginTop: 8 }}>
              <input type="email" autoComplete="email" aria-label={t("email.new")} placeholder={t("email.new")}
                value={email} onChange={(e) => setEmail(e.target.value)} />
              <input type="password" autoComplete="current-password" aria-label={t("common.yourPassword")} placeholder={t("email.passwordToConfirm")}
                value={password} onChange={(e) => setPassword(e.target.value)} />
              <div>
                <button className="btn" type="submit" disabled={busy}>{busy ? "Saving…" : "Save address"}</button>{" "}
                <button className="btn ghost" type="button" onClick={() => { setEditing(false); setError(""); }}>Cancel</button>
              </div>
            </form>
          )}
          {error && <div className="error" role="alert" style={{ marginTop: 8 }}>{error}</div>}
          {note && <div className="notice" role="status" style={{ marginTop: 8, background: "var(--ok-bg)", color: "var(--ok)" }}>{note}</div>}
        </>
      )}
    </Card>
  );
}

/** The plain-words error for a failed email change (the server's 422 means the password was wrong
 *  or the address is not valid; it deliberately does not say which). */
export function friendlyEmailError(e: unknown): Error {
  const code = (e as { code?: string } | null)?.code;
  if (code === "VALIDATION_FAILED") {
    return new Error(t("email.invalid"));
  }
  if (code === "RATE_LIMITED") return new Error(t("common.tooManyTries"));
  return e instanceof Error ? e : new Error(t("email.couldNotSave"));
}
