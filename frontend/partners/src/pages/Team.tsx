import { useState } from "react";
import { getTeam, inviteColleague, teamStep } from "../api";
import type { TeamMember } from "../types";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

function Person({ m, onChanged }: { m: TeamMember; onChanged: (said?: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(step: "suspend" | "reinstate" | "resend") {
    setBusy(true); setError("");
    try { await teamStep(m.id, step); onChanged(); }
    catch (err) { setError(err instanceof Error ? err.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <div className="appt">
      <div className="info">
        <b>{m.login} {m.you && <Pill kind="info">you</Pill>} {m.status === "suspended" && <Pill kind="warn">suspended</Pill>} {m.pending_invite && <Pill kind="info">invitation not yet accepted</Pill>}</b>
        <small>{m.roles.join(", ") || "no role"}</small>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {!m.you && (
        <div style={{ display: "flex", gap: 8 }}>
          {m.pending_invite && <button className="btn ghost" disabled={busy} onClick={() => void run("resend")}>Send the invitation again</button>}
          {m.status === "active"
            ? <button className="btn ghost" disabled={busy} onClick={() => void run("suspend")}>Suspend</button>
            : <button className="btn ghost" disabled={busy} onClick={() => void run("reinstate")}>Reinstate</button>}
        </div>
      )}
    </div>
  );
}

function Invite({ onDone }: { onDone: (said?: string) => void }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await inviteColleague(email);
      setEmail("");
      onDone(`An invitation was emailed to ${email}. They choose their own password.`);
    } catch (err) {
      const code = (err as { code?: string }).code;
      setError(code === "CONFLICT_VERSION" ? "That login already exists."
        : code === "VALIDATION_FAILED" ? "That does not look like an email address."
        : err instanceof Error ? err.message : "Could not send the invitation");
    } finally { setBusy(false); }
  }
  return (
    <form className="ref-form" onSubmit={(e) => void submit(e)}>
      <label>Colleague's email<input className="field" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" /></label>
      <small>They get the same kind of account as you, for this organisation only.</small>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy || !email.trim()}>{busy ? "Sending…" : "Send invitation"}</button>
    </form>
  );
}

export function Team() {
  const [n, setN] = useState(0);              // changing the key loads everything again
  const [said, setSaid] = useState("");       // kept here so a reload does not erase it
  return <TeamInner key={n} said={said} onChanged={(m) => { setSaid(m ?? ""); setN(n + 1); }} />;
}

function TeamInner({ onChanged, said }: { onChanged: (said?: string) => void; said: string }) {
  const team = useLoad(getTeam, 60_000);
  return (
    <>
      <div className="hero"><div>
        <h1>My team</h1>
        <p>The people in your organisation who can sign in. You can add colleagues of your own kind, suspend someone who has left, and send an invitation again.</p>
      </div></div>
      <div className="grid">
        <Card title="People">
          <Gate result={team}>{(rows) => rows.map((m) => <Person key={m.id} m={m} onChanged={onChanged} />)}</Gate>
        </Card>
        <Card title="Add a colleague">
          <Invite onDone={onChanged} />
          {said && <div className="success" role="status">{said}</div>}
        </Card>
      </div>
    </>
  );
}
