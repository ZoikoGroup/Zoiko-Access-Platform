import { useState } from "react";
import { getProgrammeActivity, getProgrammeCodes, issueProgrammeCode, revokeProgrammeCode } from "../api";
import { Card, Pill } from "../ui";
import { Gate, useLoad } from "../useLoad";

const STATE: Record<string, { text: string; kind: "ok" | "warn" | "info" }> = {
  issued: { text: "Code not used yet", kind: "warn" }, redeemed: { text: "Linked", kind: "ok" }, revoked: { text: "Revoked", kind: "info" },
};

function Codes() {
  const [n, setN] = useState(0);
  const [fresh, setFresh] = useState("");     // kept here so it survives the reload
  return <CodesInner key={n} fresh={fresh} setFresh={setFresh} onChanged={() => setN(n + 1)} />;
}

function CodesInner({ onChanged, fresh, setFresh }: { onChanged: () => void; fresh: string; setFresh: (s: string) => void }) {
  const list = useLoad(getProgrammeCodes, 60_000);
  const [member, setMember] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function make() {
    setBusy(true); setError(""); setFresh("");
    try { const made = await issueProgrammeCode(member.trim()); setFresh(`${made.member_ref}: ${made.code}`); setMember(""); onChanged(); }
    catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? "That reference is not an enrolled member."
        : code === "CONFLICT_VERSION" ? "That member is already linked. Revoke the link first if you want a new code."
        : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(false); }
  }
  async function revoke(ref: string) {
    setBusy(true); setError("");
    try { await revokeProgrammeCode(ref); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Invite a member to be counted">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input className="field" aria-label="Member reference" placeholder="An enrolled member's reference" value={member} onChange={(e) => setMember(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
        <button className="btn" disabled={busy || member.trim().length < 3} onClick={() => void make()}>Make a one-time code</button>
      </div>
      <small>Give the code to that person. They type it into their patient app and agree. Only then do their appointments count, and you never see who they are.</small>
      {fresh && <div className="success" role="status">Code for {fresh}. It is shown only now: copy it. It works for 30 days.</div>}
      {error && <div className="error" role="alert">{error}</div>}
      <Gate result={list}>
        {(d) => d.length === 0 ? <div className="empty">No codes yet.</div> : d.map((c) => (
          <div className="appt" key={c.id}>
            <div className="info"><b>{c.member_ref} <Pill kind={STATE[c.state]?.kind ?? "info"}>{STATE[c.state]?.text ?? c.state}</Pill></b></div>
            {(c.state === "issued" || c.state === "redeemed") && <button className="btn ghost" disabled={busy} onClick={() => void revoke(c.member_ref)}>Revoke</button>}
          </div>
        ))}
      </Gate>
    </Card>
  );
}

export function ProgrammeLinks() {
  const activity = useLoad(getProgrammeActivity, 120_000);
  return (
    <>
      <Card title="Appointments and visits by month">
        <Gate result={activity}>
          {(a) => (
            <>
              <small>Totals for people who linked themselves with a code. {a.linked === null ? `Fewer than ${a.min_group} linked, so nothing is shown yet.` : `${a.linked} linked.`} A month appears only when at least {a.min_group} different people contributed.</small>
              {a.months.length === 0 ? <div className="empty">Nothing to show yet.</div> : a.months.map((m) => (
                <div className="appt" key={m.month}>
                  <div className="info">
                    <b>{m.month}</b>
                    <small>{m.hidden ? `Hidden: fewer than ${a.min_group} people` : `${m.bookings} appointments booked, ${m.visits} attended`}</small>
                  </div>
                </div>
              ))}
            </>
          )}
        </Gate>
      </Card>
      <Codes />
    </>
  );
}
