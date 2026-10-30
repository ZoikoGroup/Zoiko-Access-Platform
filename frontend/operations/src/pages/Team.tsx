import { useState } from "react";
import { inviteStaff, resendInvite, setMemberStatus } from "../api";
import { getDirectory } from "../api";
import type { Directory } from "../types";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table } from "../ui";

const TYPE_LABEL: Record<string, string> = {
  clinician: "Doctor", facility_user: "Clinic staff", support: "Support", employer_user: "Employer",
  payer_user: "Payer", workload: "Integration",
};

export function Team() {
  const [version, setVersion] = useState(0);        // bump to reload after a change
  const result = useLoad(() => getDirectory(), 10_000, version);
  return <Gate result={result}>{(dir) => <View dir={dir} onChanged={() => setVersion((v) => v + 1)} />}</Gate>;
}

function View({ dir, onChanged }: { dir: Directory; onChanged: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState(dir.grantable_roles[0]?.code ?? "");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function run(key: string, work: () => Promise<unknown>, success: string) {
    setBusy(key); setError(""); setNote("");
    try { await work(); setNote(success); onChanged(); }
    catch (e) { setError(friendly(e)); }
    finally { setBusy(""); }
  }

  const roleName = (code: string) => dir.grantable_roles.find((r) => r.code === code)?.label ?? code;

  return (
    <>
      <div className="hero"><div>
        <h1>Team</h1>
        <p>The people who run this organization and what each can do.
          {dir.can_manage ? " As an administrator you can add people and switch them off." : " Only an administrator can change the team."}</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      {note && <div className="notice" style={{ background: "var(--ok-bg)", color: "var(--ok)" }}>{note}</div>}

      {dir.can_manage && (
        <div className="grid">
          <Card title="Invite someone" sub="They get an email with a link to choose their own password. You never see or share it.">
            <div className="form-row" style={{ marginTop: 0 }}>
              <input aria-label="Email address" type="email" placeholder="name@example.org" value={email}
                onChange={(e) => setEmail(e.target.value)} />
              <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}
                style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }}>
                {dir.grantable_roles.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
              </select>
              <button className="btn" disabled={busy !== "" || !email.trim() || !role}
                onClick={() => void run("invite", () => inviteStaff(email.trim(), role).then(() => setEmail("")), "Invitation sent")}>
                Send invitation
              </button>
            </div>
          </Card>
        </div>
      )}

      <div className="grid">
        <Card title={`${dir.staff.length} team member${dir.staff.length === 1 ? "" : "s"}`}>
          <Table headers={["Login", "Kind", "Role at", "Status", ...(dir.can_manage ? ["Actions"] : [])]}
            empty="Nobody yet."
            rows={dir.staff.map((m) => {
              const active = m.roles.filter((r) => r.status === "active");
              const me = m.id === dir.me;
              return [
                <span key="l">{m.login}{me && <Pill kind="info"> you</Pill>}</span>,
                TYPE_LABEL[m.type] ?? m.type,
                active.map((r) => `${roleName(r.role)} at ${r.organization_name}`).join(", ") || "none",
                m.pending_invite ? <Pill key="p" kind="warn">invited, not accepted</Pill>
                  : <Pill key="s" kind={m.status === "active" ? "ok" : "bad"}>{m.status === "active" ? "active" : "switched off"}</Pill>,
                ...(dir.can_manage ? [
                  <span key="a">
                    {!me && (
                      <button className="btn ghost" disabled={busy !== ""}
                        onClick={() => void run(m.id, () => setMemberStatus(m.id, m.status === "active" ? "suspended" : "active"),
                          m.status === "active" ? "Switched off" : "Switched back on")}>
                        {m.status === "active" ? "Switch off" : "Switch on"}
                      </button>
                    )}{" "}
                    {m.pending_invite && (
                      <button className="btn ghost" disabled={busy !== ""}
                        onClick={() => void run(`r${m.id}`, () => resendInvite(m.id), "Invitation sent again")}>
                        Resend invitation
                      </button>
                    )}
                  </span>,
                ] : []),
              ];
            })} />
        </Card>
      </div>
    </>
  );
}

function friendly(e: unknown): string {
  const text = e instanceof Error ? e.message : "That did not work";
  if (text.includes("Conflict") || text.includes("version")) return "That email already has an account here.";
  if (text.includes("Too many") || text.includes("rate")) return "Please wait a minute before asking again.";
  return text;
}
