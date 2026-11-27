import { useState } from "react";
import { t } from "./i18n";
import { Card } from "./ui";

/** The plain-words error for a failed change-password call, whichever app's ApiError it is (the
 *  classes differ per app, so it reads the error's `code` field). */
export function friendlyPasswordError(e: unknown): Error {
  const code = (e as { code?: string } | null)?.code;
  if (code === "VALIDATION_FAILED") {
    return new Error(t("password.invalid"));
  }
  if (code === "RATE_LIMITED") return new Error(t("common.tooManyTries"));
  return e instanceof Error ? e : new Error(t("password.couldNotChange"));
}

/** "Change my password", shared by the apps. The app supplies `onChange`, which calls its own API,
 *  swaps in the fresh session token and throws an Error with a plain message on failure. The
 *  server checks the current password and the new one's strength; this form only catches typing
 *  mistakes (empty fields, the two new passwords not matching) before asking. */
export function ChangePassword({ onChange }: { onChange: (current: string, next: string) => Promise<void> }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setDone(false);
    if (!current || !next) { setError(t("password.fillBoth")); return; }
    if (next !== again) { setError(t("password.notSame")); return; }
    if (next === current) { setError(t("password.notDifferent")); return; }
    setBusy(true); setError("");
    try {
      await onChange(current, next);
      setCurrent(""); setNext(""); setAgain(""); setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("password.couldNotChange"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("password.title")}
      sub={t("password.sub")}>
      <form onSubmit={(e) => void submit(e)} style={{ display: "grid", gap: 8, maxWidth: 360 }}>
        <input type="password" autoComplete="current-password" aria-label={t("password.current")} placeholder={t("password.current")}
          value={current} onChange={(e) => setCurrent(e.target.value)} />
        <input type="password" autoComplete="new-password" aria-label={t("password.new")} placeholder={t("password.new")}
          value={next} onChange={(e) => setNext(e.target.value)} />
        <input type="password" autoComplete="new-password" aria-label={t("password.repeat")} placeholder={t("password.repeat")}
          value={again} onChange={(e) => setAgain(e.target.value)} />
        {error && <div className="error" role="alert">{error}</div>}
        {done && <div className="notice" role="status" style={{ background: "var(--ok-bg)", color: "var(--ok)" }}>{t("password.done")}</div>}
        <div><button className="btn" type="submit" disabled={busy}>{busy ? t("password.changing") : t("password.change")}</button></div>
      </form>
    </Card>
  );
}
