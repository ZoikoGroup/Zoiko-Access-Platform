import { useEffect, useState } from "react";
import { Card } from "./ui";

/** "The name I am shown by": for staff, doctors and platform users, who would otherwise appear as
 *  their login. Patients see it on appointments, colleagues see it in lists. It is typed by the
 *  person and not verified by anyone: the card says so. The app supplies the three server calls. */
export function NameCard({ load, onSave, onClear }: {
  load: () => Promise<string>;
  onSave: (name: string) => Promise<string>;
  onClear: () => Promise<void>;
}) {
  const [saved, setSaved] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    load().then((n) => { setSaved(n); setText(n); })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, [load]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setNote("");
    try {
      const name = await onSave(text);
      setSaved(name); setText(name); setNote("Saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the name");
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true); setError(""); setNote("");
    try {
      await onClear();
      setSaved(""); setText(""); setNote("Removed. You are shown by your login again.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the name");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="The name I am shown by"
      sub="Patients see it next to their appointments and colleagues see it in lists, instead of your login. You type it yourself and nobody checks it, so use your real name and title.">
      {saved === null ? <div className="empty">{error || "Loading…"}</div> : (
        <form onSubmit={(e) => void save(e)} style={{ display: "grid", gap: 8, maxWidth: 360 }}>
          <input aria-label="Display name" placeholder="e.g. Dr. Asha Rao" maxLength={80} value={text}
            onChange={(e) => setText(e.target.value)} />
          {error && <div className="error" role="alert">{error}</div>}
          {note && <div className="notice" role="status" style={{ background: "var(--ok-bg)", color: "var(--ok)" }}>{note}</div>}
          <div>
            <button className="btn" type="submit" disabled={busy || !text.trim() || text.trim() === saved}>{busy ? "Saving…" : "Save name"}</button>{" "}
            {saved && <button className="btn ghost" type="button" disabled={busy} onClick={() => void clear()}>Use my login instead</button>}
          </div>
        </form>
      )}
    </Card>
  );
}

/** Plain words for a refused name (the server only says the name is not acceptable). */
export function friendlyNameError(e: unknown): Error {
  const code = (e as { code?: string } | null)?.code;
  if (code === "VALIDATION_FAILED") {
    return new Error("That name is not accepted. Use 2 to 80 characters, no email address.");
  }
  if (code === "POLICY_DENIED") return new Error("Patients and guardians do not have a display name: your name is in My Profile.");
  return e instanceof Error ? e : new Error("Could not save the name");
}
